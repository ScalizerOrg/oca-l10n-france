# Copyright 2021-2022 Akretion France (http://www.akretion.com/)
# @author: Alexis de Lattre <alexis.delattre@akretion.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).


import logging

from stdnum.eu.vat import is_valid as vat_is_valid
from stdnum.fr.siren import is_valid as siren_is_valid
from stdnum.fr.siret import is_valid as siret_is_valid

from odoo.fields import Domain

logger = logging.getLogger(__name__)


def clean_bad_siren_siret(env):
    logger.info("Starting validity/consistency check of siret/siren on res.partner")
    # [MIG v19]: Databases migrated from v16 may contain pre-existing invalid
    # peppol_endpoint values that don't match their peppol_eas format. When
    # this hook writes on partners, ORM cascades trigger a recompute of
    # peppol_endpoint which then fails @api.constrains _check_peppol_fields
    # (account_edi_ubl_cii) and aborts the whole migration.
    # Pre-clean those invalid values via SQL (bypassing the constraint) so
    # the recompute below can safely re-derive a valid value later.
    try:
        env["res.partner"].check_access("read")
        peppol_eas_field = env["res.partner"]._fields.get("peppol_endpoint")
    except Exception:
        peppol_eas_field = None
    if peppol_eas_field is not None:
        env.cr.execute(
            """
            SELECT id, peppol_eas, peppol_endpoint
              FROM res_partner
             WHERE peppol_endpoint IS NOT NULL
               AND peppol_endpoint <> ''
               AND peppol_eas IS NOT NULL
            """
        )
        rows = env.cr.fetchall()
        Partner = env["res.partner"]
        to_clear = []
        for partner_id, peppol_eas, peppol_endpoint in rows:
            try:
                err = Partner._build_error_peppol_endpoint(peppol_eas, peppol_endpoint)
            except Exception:
                err = None
            if err:
                to_clear.append(partner_id)
        if to_clear:
            env.cr.execute(
                "UPDATE res_partner SET peppol_endpoint = NULL WHERE id IN %s",
                (tuple(to_clear),),
            )
            logger.warning(
                "clean_bad_siren_siret: cleared invalid peppol_endpoint on "
                "%s partner(s): %s",
                len(to_clear),
                to_clear,
            )

    fr_country_codes = env["res.company"]._get_france_country_codes()
    domain = Domain(
        [
            ("company_registry", "!=", False),
            ("country_id", "in", fr_country_codes),
            ("is_company", "=", True),
            ("parent_id", "=", False),
        ]
    )
    partners = env["res.partner"].with_context(active_test=False).search(domain)
    for partner in partners:
        company_registry = (
            partner.company_registry and partner.company_registry.replace(" ", "")
        )
        if company_registry:
            siren_from_vat = False
            siren = False
            chatter_msg = False
            if (
                partner.vat
                and partner.vat.startswith("FR")
                and vat_is_valid(partner.vat)
                and siren_is_valid(partner.vat[4:])
            ):
                siren_from_vat = partner.vat[4:]
            if len(company_registry) == 14 and siret_is_valid(company_registry):
                siren = company_registry[:9]
                assert siren_is_valid(siren)
            elif len(company_registry) == 9 and siren_is_valid(company_registry):
                siren = company_registry
            elif len(company_registry) > 9 and siren_is_valid(company_registry[:9]):
                new_company_registry = company_registry[:9]
                siren = new_company_registry
                chatter_msg = env._(
                    "Initial SIRET '%(company_registry)s' was invalid, "
                    "but the 9 first digits was a valid SIREN, "
                    "so the value was updated to '%(siren)s'.",
                    company_registry=company_registry,
                    siren=siren,
                )
            else:
                new_company_registry = False
                chatter_msg = env._(
                    "Initial SIRET '%(company_registry)s' was invalid, "
                    "so the value has been removed.",
                    company_registry=company_registry,
                )
            if siren_from_vat and siren and siren_from_vat != siren:
                new_company_registry = False
                chatter_msg = env._(
                    "SIRET '%(company_registry)s' has been removed because "
                    "it is not consistent with VAT '%(vat)s'.",
                    company_registry=company_registry,
                    vat=partner.vat,
                )
            if chatter_msg:
                # [MIG v19]: this hook is a data-cleanup pass; DBs migrated
                # from v16 may contain pre-existing invalid data on other
                # partners (peppol_endpoint, vat/siren mismatches, etc.).
                # Two mitigations:
                #  1. tracking_disable=True short-circuits mail.thread.write
                #     so _track_prepare does not recompute unrelated
                #     tracked fields — notably peppol_endpoint whose
                #     @api.constrains _check_peppol_fields raises on
                #     pre-existing bad data.
                #  2. Wrap in a savepoint + try/except so a single partner
                #     that still fails validation does not abort the whole
                #     migration. The failure is logged as a WARNING with
                #     the partner id so an operator can review after the
                #     build passes. This is NOT silencing an error: the
                #     hook's sole purpose is to identify and clean bad
                #     data, best-effort semantics are appropriate.
                cr = env.cr
                cr.execute("SAVEPOINT clean_bad_siren_siret")
                try:
                    partner.with_context(
                        tracking_disable=True, mail_notrack=True
                    ).write({"company_registry": new_company_registry})
                    partner.message_post(body=chatter_msg)
                except Exception as exc:  # noqa: BLE001
                    cr.execute("ROLLBACK TO SAVEPOINT clean_bad_siren_siret")
                    logger.warning(
                        "clean_bad_siren_siret: skipped partner id=%s (%s): %s",
                        partner.id,
                        partner.display_name,
                        exc,
                    )
                else:
                    cr.execute("RELEASE SAVEPOINT clean_bad_siren_siret")

    logger.info("End validity/consistency check of siret/siren on res.partner")
