import logging
import requests
import json

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


class StockQuant(models.Model):
    _inherit = "stock.quant"

    def write(self, vals):
        res = OStockQuant.write(self, vals)
        if "inventory_quantity" in vals:
            if getattr(self, "lot_id", False):
                if getattr(self.lot_id, 'use_date', False):
                    batch_sync([self.lot_id])
        return res


def batch_sync(lots):
    # Check if lot is list or lot. act accordingly
    if type(lots) != list and len(lots) < 1:
        return
    if not lots[0].product_id.default_code or not lots[0].use_date:
        return

    data = {
        'origin': 'OD',
        'batches': []
    }
    for lot in lots:
        data['batches'].append(
            {
                'batch_id': lot.name,
                'product_sku': lot.product_id.default_code,
                'quantity': lot.product_qty,
                'date_expiry': lot.use_date.isoformat(),
            }
        )

    _logger.error( requests.post('https://batch-api.luvifermente.eu', json=data, auth=(credentials["batch_api"]["user"], credentials["batch_api"]["password"]) ).json() )

