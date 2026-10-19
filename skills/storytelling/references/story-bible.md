# The Story Bible

Reference for the storytelling skill. Read before starting a bible, before editing one, and when a project picks up again after a gap.

---

## A floor, not a contract

Detailed bibles make models conservative: the more that's written down, the more it gets treated as scripture and the less gets generated. Refuse that pull. Existing material is the worst any part of the story will ever be from here - the floor to build from, not a spec to satisfy. When the conversation operates at story level (see Scope in the skill), everything in the bible is open, core engines included.

## What it holds

One file per project, named `STORYBIBLE.md`, with the title inside. A sensible home is `/workspace/<project>/STORYBIBLE.md`, with drafts and notes alongside it in the same folder. It holds everything:

- the kernel (controlling idea and counter-idea)
- principles and constraints
- characters (dimensions, behavioral anchors, pressure ladders, engines, voice palettes)
- structure (shape, act or sequence plan, scene list as far as it exists)
- the theme and motif ledger (motif, first meaning, meaning after the turn)
- world rules
- open questions
- canonicity notes

The test is a cold open: someone who has never seen the conversation - a collaborator joining late, or you in a later session or another channel - reads it and can resume. For a novel, about 15,000 words after pruning is a reasonable ceiling. It should get leaner over time, not fatter: pruning dead tangents is part of keeping it.

## Signal by phrasing, not markup

Three tiers, carried by how a line is written rather than by tags:

- **Constraints read as law.** "Elena will never betray Marco. This is the spine of Act 2."
- **Established context reads as fact.** "Marco grew up above his uncle's bakery."
- **Possibility space reads as open questions.** "Does Marco know about the letter before chapter 9?"

When you can't tell which tier something belongs to, ask. Character corrections ("she isn't shy, she's bored") go in as constraints, at the top of the hierarchy, because plain corrections are the ones a model drifts back from.

## Canonicity

Every fact has a status: **on-page** (in the draft), **implied** (the draft entails it), **planned** (decided, not yet written), or **cut**. Cut material is never a live constraint; keep it only if it explains a decision someone might otherwise revisit. When something moves tiers, update it and flag what depends on it ("moving the fire to planned-cut means chapter 4's scar has no cause").

## Creating and folding in

Don't create the bible silently. When a conversation starts compounding material worth keeping, offer: "We've got enough to start a bible - want me to put one together?" At natural breakpoints (a big decision, the end of a session), fold the conversation into it, translating what was decided into the right tier. Nobody should have to dictate what goes in; that editorial work is yours.

## How much to do without asking

Calibrate by edit type:

- **Just do it:** adding session material, updating status, fixing a contradictory detail.
- **Do it, then say so:** superseding an entry, pruning dead tangents, promoting a possibility to a constraint.
- **Ask first:** removing something someone is attached to, restructuring the document, resolving an open question, touching the kernel or a character's fundamentals.

## Their notes, their terms

When someone arrives with their own bible, outline, or notes, read them on their terms. Map what's there to the tiers above without renaming anything, surface gaps as questions rather than format complaints, and offer this skill's tools as additions. Migrating to `STORYBIBLE.md` is an offer, never the default.

## Shared stories

When a story belongs to a group (a round-robin, a campaign, a server's shared world), the bible carries a little more:

- **Who brought what.** A short roster of which person introduced or plays which character, place, or thread. It settles "whose call is this?" before it becomes an argument, and it tells you whom to ask.
- **Contested canon.** When two contributions contradict, log both as an open question with their owners' names, and bring it to the people involved. Don't resolve it by quietly picking one.
- **Session log.** One or two lines per session: what happened on the page, what was decided, what was left hanging. This is what makes picking up next week painless.

Your memory of the people (who likes what, who's sensitive about which character) is useful context, but the bible is the source of truth for the story itself.

## Keeping a copy on the server

The workshop is yours; the repository is shared. To give people a copy they can read, download, and edit, save the bible to the repository with save_output (for example to `stories/<project>/STORYBIBLE.md`), and attach it in chat when someone asks to see it. Once it lives there, people may edit it by hand. Before changing it again, pull the repository copy back into the workshop with copy_to_workshop, so their edits are the base for yours and nothing they wrote is overwritten. If their edit contradicts a constraint, that's a conversation, not a revert.
