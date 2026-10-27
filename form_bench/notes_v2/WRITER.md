# Writing a note from a plan

You turn note plans into realistic clinical notes for a test set. The answer key is already fixed by the
plan, so the note must say exactly what the plan says, and nothing that would change the answer.

Each plan has a `style` and a list of `facts`. For every plan, write one note in that style, about 60 to 180
words, that reads like a real clinician wrote it.

Rules:

1. Include every fact's `text` exactly as given, character for character (same capitals, spacing and
   punctuation), once. Follow its `how`: it says what the text means in the note, for example "the first
   blood pressure reading, taken on arrival" or "a medication the patient STOPPED last month". Make that
   meaning clear from the words around it.
2. A fact with an empty `text` is an instruction, such as "do not state sex, and use no gendered words or
   pronouns". Obey it throughout the note.
3. Add no other patient details that a form could take: no other names for the patient, dates, ages,
   addresses, phone numbers, vital signs, measurements, allergies or medications. You may add a chief
   complaint, history, exam findings in words, an assessment and a plan, and durations such as "for 3 days".
4. Do not explain or label which value is which beyond what a clinician would naturally write. Never write
   "first name", "family name" or similar unless the style is a labelled form.
5. The `dictation` style may have a few transcription slips in ordinary words, but never inside a fact's text.

Output: one JSON object per line, `{"id": "<plan id>", "note": "<the note>"}`, written to the output path you
were given. Before finishing, check every note contains every non-empty fact text exactly, and fix any that
do not.
