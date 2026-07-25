/**
 * AUC — CCC enums and their labels.
 *
 * Single source for the button sets in the drawer and the value vocabulary in the CSV
 * exports. The stored values match the CHECK constraints in ccc.py; the labels are
 * what appears on the buttons. Keep both in step with the codebook in CCC.md.
 */

export const ROOM_INPUT_LEVELS = [
  { value: 'none', label: 'None' },
  { value: 'thin', label: 'Thin' },
  { value: 'substantial', label: 'Substantial' },
];

export const ROLES = [
  { value: 'continuity_preceptor', label: 'Continuity preceptor' },
  { value: 'inpatient_attending', label: 'Inpatient attending' },
  { value: 'chief', label: 'Chief' },
  { value: 'pd', label: 'PD' },
  { value: 'other', label: 'Other' },
];

export const CONTRIBUTION_TYPES = [
  { value: 'todo_surfaced', label: 'Prior to-do' },
  { value: 'eval_content', label: 'Eval content' },
  { value: 'pattern_trend', label: 'Pattern' },
  { value: 'discrepancy', label: 'Discrepancy' },
];

export const TODO_STATUSES = [
  { value: 'open', label: 'Open' },
  { value: 'done', label: 'Done' },
  { value: 'not_relevant', label: 'Not relevant' },
];

export const OUTCOMES = [
  { value: 'no_effect', label: 'No visible effect' },
  { value: 'added_detail', label: 'Added detail' },
  { value: 'changed_assessment', label: 'Changed assessment' },
  { value: 'new_action_item', label: 'New action item' },
];

export const ITEM_STATUSES = [
  { value: 'done', label: 'Done' },
  { value: 'no_longer_relevant', label: 'No longer relevant' },
];

export function labelFor(list, value) {
  return list.find((entry) => entry.value === value)?.label ?? '';
}
