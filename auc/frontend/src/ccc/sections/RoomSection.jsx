/**
 * Section B — what the room produced on its own, recorded before the operator speaks.
 *
 * The ordering matters for the study: once you have surfaced something, you can no
 * longer honestly rate what the room came up with unprompted. Hence the hint.
 *
 * Spontaneous input carries a second job: it is the marker that separates a resident who
 * was actually discussed from one whose page was opened incidentally. Tapping "None" is
 * therefore a real answer and must not be skipped — leaving it blank is what tells the
 * export this row was never a discussion. Hence the "always tap one" hint on the field.
 */

import React from 'react';
import { ROOM_INPUT_LEVELS, ROLES } from '../cccConstants';
import { AutoText, ChipRow, ChoiceRow, Field, Section, YesNo } from '../CccFields';

export default function RoomSection({ values, patch }) {
  return (
    <Section title="The room" hint="Fill this in before you speak">
      <Field label="Spontaneous input — always tap one, even None">
        <ChoiceRow
          size="lg"
          options={ROOM_INPUT_LEVELS}
          value={values.room_input_level ?? null}
          onChange={(v) => patch({ room_input_level: v })}
        />
      </Field>

      <Field label="Who spoke">
        <ChipRow
          options={ROLES}
          values={values.roles_spoke || []}
          onChange={(v) => patch({ roles_spoke: v })}
        />
      </Field>

      <Field label="Anyone cite a written evaluation?">
        <YesNo
          value={values.referenced_written_eval ?? null}
          onChange={(v) => patch({ referenced_written_eval: v })}
        />
      </Field>

      <Field label="What the room raised on its own">
        <AutoText
          multiline
          rows={3}
          value={values.room_raised_notes}
          placeholder="Their words, roughly"
          onSave={(text) => patch({ room_raised_notes: text })}
        />
      </Field>
    </Section>
  );
}
