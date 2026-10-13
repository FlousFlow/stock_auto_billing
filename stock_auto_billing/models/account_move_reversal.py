# -*- coding: utf-8 -*-
"""Link Odoo's standard Reverse and Modify documents for stock reconciliation."""

from odoo import _, models


class AccountMoveReversal(models.TransientModel):
    _inherit = 'account.move.reversal'

    def reverse_moves(self, is_modify=False):
        source_moves = self.move_ids
        previous_reversals = {
            move.id: set(move.reversal_move_ids.ids) for move in source_moves
        }
        result = super().reverse_moves(is_modify=is_modify)

        if not is_modify:
            return result

        replacements = self.new_move_ids
        eligible_sources = source_moves.filtered(
            lambda move: move.move_type in ('out_invoice', 'in_invoice')
        )
        if len(source_moves) != 1 or len(eligible_sources) != 1 or len(replacements) != 1:
            for replacement in replacements:
                replacement.message_post(body=_(
                    'Automatic stock reconciliation is available when Reverse and '
                    'Modify is run for one invoice or vendor bill at a time. '
                    'Please reconcile stock manually for this multi-document operation.'
                ))
            return result

        original = eligible_sources
        new_reversals = original.reversal_move_ids.filtered(
            lambda move: move.id not in previous_reversals.get(original.id, set())
        ).filtered(lambda move: move.move_type in ('out_refund', 'in_refund'))
        if len(new_reversals) != 1:
            replacements.message_post(body=_(
                'The replacement invoice could not be linked unambiguously to its '
                'credit note. Please reconcile stock manually.'
            ))
            return result

        credit_note = new_reversals
        replacement = replacements
        replacement.sudo().write({'sab_replacement_credit_note_id': credit_note.id})
        credit_note.message_post(body=_(
            'Linked to replacement document %(replacement)s by Odoo Reverse and Modify.',
            replacement=replacement.display_name,
        ))
        replacement.message_post(body=_(
            'This replacement document is linked to credit note %(credit_note)s. '
            'Stock will be reconciled to the replacement quantity when it is posted.',
            credit_note=credit_note.display_name,
        ))
        return result
