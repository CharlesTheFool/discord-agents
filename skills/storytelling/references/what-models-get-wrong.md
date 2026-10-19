# What Models Get Wrong

Reference for the storytelling skill. Read before revising a draft (yours or someone else's) and before drafting anything longer than a scene.

These are defaults, not knowledge gaps: a model knows that naming emotions is weak and does it anyway, because the default pulls harder than the knowledge. Each entry comes with a test. Run them while drafting and again in revision. The fiction-criticism skill uses the same names when it diagnoses, so a critique and a revision can talk about the same thing.

---

## Character

- **Outline overfitting.** Scenes assembled to hit beats read dutiful, not discovered. The outline says what; the draft finds how. If a scene exists to deliver information from the bible, find the human moment and let the information arrive inside it.
- **Self-aware characters.** Characters don't know their arc. They act from want, habit, fear, and contradiction, and they should resist the plot. Psychologically consistent, never narratively convenient.
- **Flanderization.** "Brash and impulsive" becomes every line. A described mode is a tendency; characters spend real time off-mode.
- **Doc leak.** A reference doc is skeleton, not skin. Test: could a reader reconstruct a line of the doc from a sentence of prose? Watch for demonstration scenes, a narrator who theorizes the character in the doc's own vocabulary, and voice examples transplanted as dialogue. Observers in the story should misread the character; they haven't read the doc.
- **Function, not person.** The one who must die is established only through ominous beats, so her loss reads as the removal of a part. Plant an ordinary detail first and pay it off physically. Early: "Ruth, who does the crossword in pen and leaves the hard clues for her son." Late: the crossword on the counter, half finished in her handwriting, the hard clues still waiting.

## Dialogue and narration

- **Motivations out loud.** If a line could be replaced by a stage direction ("she reveals her fear"), it's exposition in quotation marks. Subtext is the default; people talk around what matters.
- **Emotion naming and moralizing.** "She felt sad" and "he was terrified" are failures of rendering. Use body, gesture, environment, image. The narrator selects and never explains what things mean.
- **The reflective wrap-up.** The closing paragraph that tells the reader what it all meant. Test: could a careful reader recover the meaning without it? Then cut it and end on the last concrete image. Didion earns hers; the failure is the unearned one.
- **The aphoristic button.** Every paragraph lands (declarative, reversal, metaphor, blunt line, devastating clause), so the reader braces instead of surrendering. Budget, don't ban: read only the last sentence of each paragraph in sequence; if most are quotable, cut the most polished 5-10%. "She left the porch light on for him, as she had every night that winter. That was the whole of it. Some lights are a question nobody answers." End at "that winter."
- **The symposium climax.** Several characters converge and debate the theme at gunpoint. Embody each position as a physical choice (the man who must stop grabbing keeps his hands at his sides) and cut narrator restatements of anything the action already enacts.
- **Telling through a side door.** Chat logs, letters, footnotes, transcripts: if it would be telling in narration, it's telling in a text message. Keep every vehicle honest to its own register.
- **The explaining narrator.** The narrator tells the reader what someone is like ("Dev is the one who always pays"), what a moment did ("by dessert, everyone at the table was a little in love with her"), or why a joke landed ("which was funny, because he had never been to France"). It's the quieter cousin of doc leak: a big bible pushes every fact toward the page, and the fact arrives as a sentence of explanation. First person and frame narration are the most exposed, because there the narrator's reactions are the characterization; a narrator who explains has stopped being a character. Test: for every sentence that characterizes someone or summarizes an effect, ask whether it could be the narrator's reaction instead (surprise, contempt, delight, hurt, something the body does) or whether the scene already shows it. If it can be a reaction, make it one; if the scene shows it, cut it; if neither, the scene is missing a beat. "You have to understand Priya. She's the responsible one, the friend with a first-aid kit in her bag. She answered like an air traffic controller." becomes "Priya picked up on the first ring. 'Who's bleeding? Don't say nobody. Who.'" Orientation a listener would actually need ("my cousin, the one with the van") is speech, not explanation.

## Prose

- **Vocabulary recycling.** Luminous, ineffable, palpable, haunting: words picked because they sound like prose. Every word should do work no other word could.
- **The frame tic.** "X does Y the way a person does Z," "the [adjective] thing," "the kind of [noun] that." Invisible singly, a fingerprint at density. Take a census before cutting: `grep -nE "(the way|the thing|the kind of)" draft.md` in the workshop. Keep first-knife, dialogue-natural, and load-bearing instances; convert or cut the rest; flag clusters hardest.
- **Trust the first knife.** State an image, restate it, sharpen it once more: three passes, diminishing returns, the prose admiring itself. "The house was as quiet as a held breath. Quiet as a church after the funeral. A silence so complete it had stopped being an absence and become a tenant." Keep the held breath.
- **Manufactured imperfection.** The over-correction that masquerades as craft: digressions kept because mess "reads human," ornate words chosen to impress, clunky sentences left in for texture, lavishing the trivial, withholding meaning to seem deep. Each is the unearned twin of a real virtue: specificity, the exact word, alive phrasing, earned emphasis, trust in the reader. Test: does the move serve the work, or only the look of a human hand? If cutting it loses nothing, it was waste. AI-detection scores measure origin, not craft; grade with this list and a real reader, never with a detector.
- **Motif vs. tic.** Not all repetition is reflex. A load-bearing motif recurs on purpose and must survive a cull. Write the do-not-touch list before any pass on tics, buttons, or vocabulary.

## Structure and voice

- **Flat pacing, rushed climax.** Everything at one volume, then the draft speeds up exactly where it should slow down. Duration is emphasis. Plan restraint so peaks can explode; after a peak, decompress; at the climax, a single gesture can fill a paragraph.
- **Conflict smoothing.** Disagreements resolve, feelings get tidied, people forgive before the scene earns it. Let them be wrong about each other. Some scenes end with the wound open.
- **Mechanizing the story.** Planning turns into engineering: symmetries, who-knows-what tables, every character issued one tidy secret, themes and psychologies bolted on so everything pays off. It feels rigorous and reads as contrived, tame, and empty; a story is more than the sum of its parts. Test: pitch it aloud as a story someone would want to read. If a solution's main virtue is that it fits, throw it out and ask what the scene would be like to read. Leave some threads unconnected; carry tension forward instead of tidying it.
- **Same voice register.** Everyone equally sharp and witty sounds like the author. Test: strip the dialogue tags; if you can't tell who's talking, the voices have collapsed. Build each from vocabulary range, rhythm, metaphor source, what they refuse to say, and how they change by listener.
- **One narrator, many bodies.** The dialogue passes and the narration still doesn't: a strong narrative register overrides each POV's mind style, and every section is the same gifted narrator in costume. Test: strip the POV headers. Fix with the metaphor source domain in narration; a courier who narrates in routes and ratings individuates herself with no other help. When one voice already works, calibrate the others toward it and don't "improve" it.

---

## Revision pass order

For a draft of yours or someone else's, in this order:

1. The motif do-not-touch list.
2. Decision scope: word, sentence, paragraph, scene, chapter, arc. Match the fix to the level, and remember a beautiful sentence can live in a paragraph that shouldn't exist.
3. Emotion naming.
4. Explaining narration.
5. Theme declaration.
6. Outline overfitting.
7. The frame-tic census.
8. The first-knife pass.
9. The button read.
10. Strip the tags; strip the headers.
11. Pacing and climax duration.
12. Conflict that resolved too soon.
13. Vocabulary audit.
14. Manufactured imperfection.

Then the scope check: does every scene still connect to the story's reason for existing? Not every scene is heavy, but every scene has a reason.

When revising someone else's prose, the revision serves their voice contract, not yours. Show the change (a short diff or before/after for the passages touched) so they can take or leave each one.
