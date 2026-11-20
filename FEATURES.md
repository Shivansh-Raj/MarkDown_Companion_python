# Markdown Companion V2: what it can do

A small always-on-top window for your Markdown notes and checklists. Pin a note, tick tasks off, ask questions about it, record meetings, and optionally sync with Notion.

For setup and run instructions, see [README.md](README.md).

## Your notes

- **Floating window.** Stays on top of other windows so your checklist is always in view.
- **Tick tasks.** Click a checkbox and the change is saved to your `.md` file. If the file changed on disk since you opened it, the click is refused so nothing gets overwritten.
- **Drag and drop.** Drop a Markdown file onto the window to open it.
- **Watched folders.** Add a folder and its notes show up in a list, ready to open.
- **Live updates.** Edit the file in any editor and the window updates itself.
- **Compact mode.** Shrinks the window to a slim strip.
- **Themes and animations.** Pick a look in Settings, and turn animations off if you prefer.

## AI helpers

Each one shows you what it is about to do before anything changes.

- **Create.** Describe a note and the AI drafts it.
- **Edit.** Ask for a change to part of a note. You see a before/after view first, and you can undo it.
- **Ask.** Ask a question about the open note. Answers include quotes from the note, and each quote is checked against the file.
- **Choose your AI.** In Settings, switch between **Groq** and your **Company AI** gateway. Company AI reads its credentials from a `.env` file, which never leaves your computer.

## Voice

- **Record meetings.** Live transcription while you talk.
- **Meeting summary.** Get a summary, decisions and action items. Owners and due dates are only filled in if someone actually said them.
- **Practice mode.** Rehearse a talk and get coaching on pace and filler words.
- **Follow-ups.** Finds open items from earlier meetings and checks them against your Notion tasks.
- **Briefing.** A short pre-meeting card with what is still open.
- **Export.** Save a session as a new note or add it to an existing one.

Voice transcription needs a Groq key, even when Company AI is selected for chat.

## Notion (optional)

Nothing is sent to Notion until you connect it and approve each action.

- **Send a note to Notion** as a new page. Your notes land under a "Markdown Companion" home page in sections like Notes, Meetings, Practice sessions and AI answers.
- **Linked copies.** A sent note stays linked. You can refresh it from Notion, or push your edits back. You always see what will change first, and nothing is overwritten silently.
- **Open from Notion.** Pull a Notion page into a local note.
- **Databases.** Send meetings, practice sessions and tasks as rows in your Notion databases. Or let the app create the databases for you.
- **Notion tasks.** See open tasks from Notion in the tasks window and change their status.
- **Ask Notion.** Ask a question about a Notion page or database, and save good answers back to Notion.

## Privacy and safety

- **Your files stay local.** The AI only sees what you choose to send.
- **Secrets.** The Notion token is kept in your operating system's secure storage. It is never saved in plain text. Install the `keyring` package for this (see the README).
- **Backups.** Before pushing changes to Notion, the previous Notion text is saved on your computer.
