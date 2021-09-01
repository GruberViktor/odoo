import logging

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression
from odoo.tools.float_utils import float_compare, float_is_zero, float_repr, float_round
from odoo.tools.misc import format_date, OrderedSet
from odoo.api import Environment as env

_logger = logging.getLogger(__name__)

def log(*text):
    text = " ".join([str(t) for t in text])
    _logger.error(text)

class StockApi(models.Model):
    _name = "stock.api"

    @api.model
    def get_lot_info(self, vals):
        """
        Returns the best before date of the current batch of a product specified by its SKU
        """
        val = vals[0]
        if val[0].lower() != "sku":
            return False

        product = self.env['product.product'].search([['default_code','=',val[2]]])
        if len(product) == 0:
            return False
        product = product[0]

        lot = self.env['stock.quant'].search(['&',('product_id', '=', product['id']),('on_hand', '=', True)], order='removal_date asc')
        if len(lot) == 0:
            return False
        
        return lot[0]['removal_date']

    @api.model
    def reduce_lots(self, vals):
        log(vals)
        for product_req in vals:
            product = self.env['product.product'].search(
                [['default_code','=', product_req['sku']]]
                )
            if len(product) == 0:
                continue
            
            lots = self.env['stock.quant'].search(
                ['&',('product_id', '=', product[0]['id']),('on_hand', '=', True)], 
                order='removal_date asc'
                )

            move = self.env['stock.move'].create({
                'name': product_req['order'],
                'location_id': 8,
                'location_dest_id': 14,
                'product_id': product.id,
                'product_uom': product.uom_id.id,
                'product_uom_qty': product_req['qty'],
            })
            move._action_confirm()
            move._action_assign()
            product_req['lots'] = []
            for line in move.move_line_ids:
                line.write({'qty_done': line['product_uom_qty']})
                product_req['lots'].append({
                    '_qty': line['product_uom_qty'],
                    '_lot_id': line.lot_id.name,
                    '_best_before': line.lot_id.removal_date
                })
            move._action_done()

        return vals