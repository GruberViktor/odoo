import logging
import woocommerce

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression
from odoo.tools.float_utils import float_compare, float_is_zero, float_repr, float_round
from odoo.tools.misc import format_date, OrderedSet
from odoo.api import Environment as env

from odoo.addons.stock.models.stock_move import StockMove as OStockMove
from odoo.addons.stock.models.stock_quant import StockQuant as OStockQuant
from odoo.addons.stock.models.stock_production_lot import ProductionLot as OProductionLot

_logger = logging.getLogger(__name__)


class StockMove(models.Model):
    _inherit = "stock.move"
    _last_id = 0

    def _set_lot_ids(self):
        for move in self:
            move_lines_commands = []
            if move.picking_type_id.show_reserved is False:
                mls = move.move_line_nosuggest_ids
            else:
                mls = move.move_line_ids
            mls = mls.filtered(lambda ml: ml.lot_id)
            for ml in mls:
                if ml.qty_done and ml.lot_id not in move.lot_ids:
                    move_lines_commands.append((2, ml.id))
            ls = move.move_line_ids.lot_id
            for lot in move.lot_ids:
                if lot not in ls:
                    move_line_vals = self._prepare_move_line_vals(quantity=0)
                    move_line_vals['lot_id'] = lot.id
                    move_line_vals['lot_name'] = lot.name
                    move_line_vals['product_uom_id'] = move.product_id.uom_id.id
                    move_line_vals['qty_done'] = 0
                    move_lines_commands.append((0, 0, move_line_vals))
            move.write({'move_line_ids': move_lines_commands})
    
    def write(self, vals):
        res = OStockMove.write(self, vals)
        if self.lot_ids:
            if self._last_id != self.id:
                type(self)._last_id = self.id
                batch_sync(self.lot_ids)
        _logger.error(str(self.is_done) + " " + str(self.lot_ids))
        return res

# class BatchUpdate(models.Model):
#     _name = "stock.production.lot.update"
#     _order = 'sequence, id'




class StockQuant(models.Model):
    _inherit = "stock.quant"

    def write(self, vals):
        res = OStockQuant.write(self, vals)
        if "inventory_quantity" in vals:
            if getattr(self, "lot_id", False):
                if getattr(self.lot_id, 'use_date', False):
                    batch_sync(self.lot_id)
        return res


def batch_sync(lot):
    # Check if lot is list or lot. act accordingly
    if not lot.product_id.default_code or not lot.use_date:
        return

    _credentials = [ 
        {
            "url": "https://staging.fermentationculture.eu",
            "consumer_key": "ck_920046968f9abe2d853b449e667bad8f9f7e8c00",
            "consumer_secret": "cs_ecc80b09d32fd448085401d0207e0a140221e453"
        },
        {
            "url": "https://staging.luvifermente.eu",
            "consumer_key": "ck_e870d5282dceec9e2577fc87e8a295b52b548f3d",
            "consumer_secret": "cs_67b496ea79f9c78623275db91d9a7dbbf1040892"
        }]
    
    _shops = []
    for _cred in _credentials:
        _shops.append(
            woocommerce.API(
                url= _cred["url"], 
                consumer_key= _cred["consumer_key"], 
                consumer_secret= _cred["consumer_secret"],
                wp_api= True,
                version= "wc/v3",
                timeout= 40
            )
        )

    data = {
        'product_sku': lot.product_id.default_code,
        'quantity': lot.product_qty,
        'date_expiry': lot.use_date.isoformat()
    }

    for shop in _shops: 

        shop.put("batches/id/" + lot.name, data)
