import logging
import requests
import json
import time
import os


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

with open(os.path.dirname(os.path.abspath(__file__)) + "/credentials.json", "r") as file:
    credentials = json.loads(file.read())


class StockMove(models.Model):
    _inherit = "stock.move"
    _last_id = 0

    def _set_lot_ids(self):
        # _logger.error("_set_lot_ids Start")
        # _logger.error(self[0].move_line_ids)
        for move in self:
            move_lines_commands = []
            if move.picking_type_id.show_reserved is False:
                mls = move.move_line_nosuggest_ids
            else:
                mls = move.move_line_ids
            mls = mls.filtered(lambda ml: ml.lot_id)
            _logger.error(mls)
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
                    move_line_vals['qty_done'] = self.quantity_done
                    move_lines_commands.append((0, 0, move_line_vals))
            move.write({'move_line_ids': move_lines_commands})

        # _logger.error("End")
        # _logger.error(self[0].move_line_ids)

    @api.depends('move_line_ids', 'move_line_ids.lot_id', 'move_line_ids.qty_done')
    def _compute_lot_ids(self):
        # _logger.error("_compute_lot_ids Start")
        # _logger.error(self[0].move_line_ids)
        domain_nosuggest = [('move_id', 'in', self.ids), ('lot_id', '!=', False), '|', ('qty_done', '!=', 0.0), ('product_qty', '=', 0.0)]
        domain_suggest = [('move_id', 'in', self.ids), ('lot_id', '!=', False), ('qty_done', '!=', 0.0)]
        lots_by_move_id_list = []
        for domain in [domain_nosuggest, domain_suggest]:
            lots_by_move_id = self.env['stock.move.line'].read_group(
                domain,
                ['move_id', 'lot_ids:array_agg(lot_id)'], ['move_id'],
            )
            lots_by_move_id_list.append({by_move['move_id'][0]: by_move['lot_ids'] for by_move in lots_by_move_id})
        for move in self:
            move.lot_ids = lots_by_move_id_list[0 if move.picking_type_id.show_reserved else 1].get(move._origin.id, [])
        # _logger.error("_compute_lot_ids End")

    def write(self, vals):
        res = OStockMove.write(self, vals)
        # if self.lot_ids and self.product_id.default_code:
        batch_sync(self.lot_ids)
        return res


class StockQuant(models.Model):
    _inherit = "stock.quant"

    def write(self, vals):
        res = OStockQuant.write(self, vals)
        if "inventory_quantity" in vals:
            if getattr(self, "lot_id", False):
                if getattr(self.lot_id, 'use_date', False):
                    batch_sync([self.lot_id])
                    time.sleep(1)   # Sometimes the function is called twice within a very short time. 
                                    # The receiving servers haven't saved the batch yet, so they won't 
                                    # update the existing one, but create a new one, resulting in duplicates.
        return res


class Picking(models.Model):
    _inherit = "stock.picking"

    def action_done(self):
        return self._action_done()


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
                'date_expiry': lot.removal_date.isoformat(),
            }
        )

    requests.post('https://batch-api.luvifermente.eu', json=data, auth=(credentials["batch_api"]["user"], credentials["batch_api"]["password"]) )

