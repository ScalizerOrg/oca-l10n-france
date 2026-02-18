# Copyright 2019-2020 Akretion France (http://www.akretion.com/)
# @author: Alexis de Lattre <alexis.delattre@akretion.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    def _get_bank_statements_available_import_formats(self):
        res = super()._get_bank_statements_available_import_formats()
        res.append("CFONB")
        return res

    def _parse_bank_statement_file(self, raw_file):
        """Bridge Enterprise import system to OCA CFONB wizard."""
        wizard = self.env["account.statement.import"]
        if not wizard._check_cfonb(raw_file):
            return super()._parse_bank_statement_file(raw_file)
        return wizard._parse_file(raw_file)
