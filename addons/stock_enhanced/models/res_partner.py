# -*- coding: utf-8 -*-
import logging
import requests
import json
import os
import re
from datetime import date
from dateutil.relativedelta import relativedelta

from . import emails

from odoo import api, fields, models, tools, SUPERUSER_ID, _
from odoo.modules import get_module_resource
from odoo.osv.expression import get_unaccent_wrapper
from odoo.exceptions import UserError, ValidationError

def log(*text):
    text = " ".join([str(t) for t in text])
    logging.getLogger(__name__).error(text)

with open(os.path.dirname(os.path.abspath(__file__)) + '/credentials.json', 'r') as file:
    credentials = json.loads(file.read())

class Partner(models.Model):
    # _name = "res.partner_enhanced"
    _inherit = "res.partner"

    type = fields.Selection(selection_add=[('shop', 'Verkaufsort')])
    shop_name = fields.Char()
    latitude = fields.Char()
    longitude = fields.Char()

    shipping_note = fields.Char()
    

class Geocoder(models.Model):
    _name = "res.partner.geocode"

    def geocode(self):
        shops_without_location = self.env['res.partner'].search([('type', '=', 'shop'),('latitude','=',False)])
        for shop in shops_without_location:
            # api_query = requests.request(
            #     'get',
            #     'https://maps.googleapis.com/maps/api/geocode/json?address=' 
            #         + shop.contact_address.replace(" ", "+").replace("\n", ",+")
            #         + '&key=' + credentials['google_cloud']
            #     ).json()
            api_query = requests.request(
                'get',
                'https://maps.googleapis.com/maps/api/geocode/json?address=' 
                    + shop.shop_name.replace(" ", "+").replace('&', 'und') + "," + shop.street.replace(" ", "+") + "," + shop.city.replace(" ", "+") + shop.country_id.code
                    + '&key=' + credentials['google_cloud']
                ).json()
            try:
                for i in range(len(api_query['results'])):
                    address_components = api_query['results'][i]['address_components']
                    correct_address = False
                    for comp in address_components:
                        if 'country' in comp['types']:
                            if comp["short_name"] == shop.country_id.code:
                                correct_address = True
                    if not correct_address:
                        continue

                    
                    coordinates = api_query['results'][i]['geometry']['location']
                    shop.latitude = str(coordinates['lat'])
                    shop.longitude = str(coordinates['lng'])

                    for comp in address_components:
                        if 'administrative_area_level_1' in comp['types']:
                            state = self.env['res.country.state'].search([('name','=',comp['long_name'])])
                            shop.state_id = state.id
                    break
            except:
                emails.send_email("[Odoo] Es gab ein Problem bei der Aktualisierung von " + shop.shop_name, "")

        return "Updated " + str(len(shops_without_location)) + " shops"

    def list_shops(self):
        self.geocode()
        shop_list = {}
        shops = self.env['res.partner'].with_context(lang="de_DE").search([('type','=','shop')])
        date_months_ago = (date.today() + relativedelta(months=-12)).strftime("%Y-%m-%d")
        for shop in shops:
            country = str(shop.country_id.name)
            state = str(shop.state_id.name)
            if not shop_list.get(country):
                shop_list[country] = {}
            if not shop_list[country].get(state):
                shop_list[country][state] = []

            # orders = self.env['sale.order'].search([('partner_id', '=', shop.parent_id.id),('date_order','>',date_months_ago)])
            orders = self.env['stock.picking'].with_context(lang="de_DE").search([
                ('partner_id', 'child_of', shop.parent_id.id),
                ('date_done','>',date_months_ago)])
            if len(orders) == 0:
                continue

            bought_products = []
            for order in orders:
                bought_products.extend([re.sub(r'(Bio |\s\-.*$)', '', line.product_id.name) for line in order.move_line_ids if re.match(r'^\d', str(line.product_id.default_code))])
            bought_products = [{"product": product} for product in list(set(bought_products))]
            
                

            shop_object = {
                "name": shop.shop_name,
                "address": re.sub(r'^[^\n]*\n', '', re.sub(r'(?<!\S)\n', '', shop.contact_address)).replace("\n", "<br>"),
                "street": shop.street,
                "city": shop.city,
                "zip": shop.zip,
                "products": bought_products,
                "lat": shop.latitude,
                "lng": shop.longitude,
                "website": shop.parent_id.website,
                "text": shop.comment if shop.comment is not False else ""
            }
            shop_list[country][state].append(shop_object)
        
        return shop_list