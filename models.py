from openerp import models, fields, api, _
from openerp.osv import osv
from openerp.exceptions import except_orm, ValidationError
from StringIO import StringIO
import urllib2, httplib, urlparse, gzip, requests, json
import openerp.addons.decimal_precision as dp
import logging
import time
import datetime
from openerp.fields import Date as newdate
from datetime import datetime

#Get the logger
_logger = logging.getLogger(__name__)


class pos_config(models.Model):
	_inherit = 'pos.config'

	refund_journal_id = fields.Many2one('account.journal',string='Diario NC',domain=[('type','=','sale_refund')])

class pos_order(models.Model):
	_inherit = 'pos.order'

	sale_order_id = fields.Many2one('sale.order')

	def refund(self, cr, uid, ids, context=None):
        	"""Create a copy of order  for refund order"""
	        clone_list = []
        	line_obj = self.pool.get('pos.order.line')

		refund_journal = None
		for order in self.browse(cr, uid, ids, context=context):
			current_session_ids = self.pool.get('pos.session').search(cr, uid, [
		                ('state', '!=', 'closed'),
                		('user_id', '=', uid)], context=context)
			if not current_session_ids:
				raise osv.except_osv(_('Error!'), _('To return product(s), you need to open a session that will be used to register the refund.'))
			
			if not order.session_id.config_id.refund_journal_id:
				raise osv.except_osv(_('Error!'), _('Falta definir el journal para las devoluciones'))
			clone_id = self.copy(cr, uid, order.id, {
				'name': order.name + ' REFUND', # not used, name forced by create
				'sale_journal': order.session_id.config_id.refund_journal_id.id,
				'session_id': current_session_ids[0],
				'date_order': time.strftime('%Y-%m-%d %H:%M:%S'),
				}, context=context)
			refund_journal = order.session_id.config_id.refund_journal_id.id
			clone_list.append(clone_id)

		for clone in self.browse(cr, uid, clone_list, context=context):
			self.pool.get('pos.order').write(cr,uid,clone.id,{'sale_journal': refund_journal})
			for order_line in clone.lines:
				line_obj.write(cr, uid, [order_line.id], {
					'qty': -order_line.qty
					}, context=context)

	        abs = {
	            'name': _('Return Products'),
        	    'view_type': 'form',
	            'view_mode': 'form',
        	    'res_model': 'pos.order',
	            'res_id':clone_list[0],
        	    'view_id': False,
	            'context':context,
        	    'type': 'ir.actions.act_window',
	            'nodestroy': True,
        	    'target': 'current',
	        }
        	return abs



class sale_order(models.Model):
	_inherit = 'sale.order'

	@api.one
	def create_pdv_ticket(self):
		# Checks if a session is open

		if self.ticket_id:
			raise ValidationError('Ya hay ticket creado para el pedido')
		if not self.user_id.pos_config:
			raise ValidationError('No existe sucursal definida para el usuario.\nPor favor contacte al administrador')
		session_id = self.env['pos.session'].search([('state','=','opened'),('config_id','=',self.user_id.pos_config.id)])
		if not session_id:
			raise ValidationError('No hay sesion abierta')
		vals_pos_order = {
			'session_id': session_id.id,
			'name': self.name,
			'partner_id': self.partner_id.id,
			'sale_journal': session_id.config_id.sale_journal_id.id,
			'location_id': session_id.config_id.stock_location_id.id,
			'user_id': self.user_id.id,
			'pos_reference': self.client_order_ref,	
			'sale_order_id': self.id,
			}
		pos_order = self.env['pos.order'].create(vals_pos_order)	
		for line in self.order_line:
			vals_line = {
				'order_id': pos_order.id,
				'discount': line.discount,
				'display_name': line.name,
				'name': line.name,
				'price_subtotal': line.price_subtotal,
				'price_unit': line.price_unit,
				'qty': line.product_uom_qty,
				'product_id': line.product_id.id,
				}
			pos_order_line = self.env['pos.order.line'].create(vals_line)
		for picking in self.picking_ids:
			picking.action_cancel()


	ticket_id = fields.One2many(comodel_name='pos.order',inverse_name='sale_order_id')


class pos_deposit(models.Model):
	_name = 'pos.deposit'
	_description = 'Deposito de efectivo en banco'

	@api.multi
	def process_pos_deposit(self):
		self.write({'state': 'process'})
		return None

	@api.multi
	def finish_pos_deposit(self):
		self.write({'state': 'deposited'})
		return None

	name = fields.Char('Nombre del deposito',required=True)
	user_id = fields.Many2one('res.users',string='Empleado que realiza operacion',required=True)
	date = fields.Date('Fecha',required=True)
	monto = fields.Float('Monto',required=True)
        state = fields.Selection(selection=[('draft','Borrador'),('process','En Proceso'),('deposited','Depositado')],string='Status',default='draft')
	nro_deposito = fields.Char(string='Nro Deposito')
