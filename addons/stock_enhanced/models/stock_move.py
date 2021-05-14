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

def log(*text):
    text = " ".join([str(t) for t in text])
    _logger.error(text)

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

    def get_orders(self):
        state = {
            "draft": "pending",
            "waiting": "on-hold",
            "confirmed": "processing",
            "assigned": "processing",
            "done": "completed",
            "cancel": "canceled"
            }
        def _get_address(partner_id):
            return {
                "first_name": partner_id.name.split()[0] if partner_id.name != False else "",
                "last_name": partner_id.name.split()[1] if partner_id.name != False else "",
                "company": partner_id.commercial_company_name if partner_id.commercial_company_name else "",
                "address_1": partner_id.street if partner_id.street else "",
                "address_2": partner_id.street2 if partner_id.street2 else "",
                "city": partner_id.city if partner_id.city else "",
                "state": "",
                "postcode": partner_id.zip if partner_id.zip else "",
                "country": partner_id.country_id.code if partner_id.country_id.code else "",
                "email": partner_id.email if partner_id.email else "",
                "phone": partner_id.phone_sanitized if partner_id.phone_sanitized else ""
            }
        orders = []
        
        for picking in self:
            order = {
                "payment_method": "Rechnung",
                "created_via": "Odoo",
                "total": "0.00",
                "invoice_ids": [],
                "line_items": [],
                "meta_data": [],
                "fee_lines": [],
                "customer_note": "",
                "website": self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            }
            
            picking_id = isinstance(picking.id, int) and picking.id or getattr(picking, '_origin', False) and picking._origin.id
            if picking_id:
                picking = self.env['stock.picking'].search([('id', '=', picking_id)])
                
                ### Number and state ###
                order["number"] = picking.name[7:]
                order["id"] = picking_id
                order["status"] = state[picking.state]
                ### Order Total and Invcoice ###
                if picking.sale_id:
                    order["total"] = "{:0.2f}".format(picking.sale_id.amount_total)
                    # if picking.sale_id.invoice_ids:
                    #     order["invoice_ids"].extend(str(picking.sale_id.invoice_ids.id))
                ### Dates ###
                order["date_completed"] = picking.date_done.isoformat() if picking.date_done else ""
                order["date_created"] = picking.create_date.isoformat()
                order["date_modified"] = picking.write_date.isoformat()
                ### Address ###
                order["shipping"] = _get_address(picking.partner_id)
                order["billing"] = _get_address(picking.partner_id)
                ### Products ###
                moves = self.env['stock.move'].search([('picking_id', '=', picking_id)])
                for move in moves:
                    for line in move.move_line_ids:
                        item = {
                            "name": line.product_id.name,
                            "quantity": int(line.product_qty),
                            "sku": line.product_id.default_code if line.product_id.default_code else "",
                            "meta_data": []
                        }
                        if line.lot_id:
                            item["meta_data"].append({
                                "key": "_batch_id",
                                "value": line.lot_id.name
                            })
                            item["meta_data"].append({
                                "key": "Charge",
                                "value": line.lot_id.name
                            })
                            if line.lot_id.removal_date:
                                item["meta_data"].append({
                                    "key": "_date_expiry",
                                    "value": line.lot_id.removal_date.isoformat()
                                })

                        order["line_items"].append(item)
                ### Meta Data ###
                order["meta_data"].append({"key": "_order_number", "value": order["number"]})
                order["meta_data"].append({"key": "order_notes", "value": picking.note if picking.note else ""})
                if picking.carrier_tracking_ref:
                    order["meta_data"].append({"key": "_tracking_code", "value": picking.carrier_tracking_ref})

            
            orders.append(order)

        return orders

    def set_done(self):
        for picking in self:
            for line in picking.move_line_ids:
                line.qty_done = line.product_qty
            picking.button_validate()
        return picking.state


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

