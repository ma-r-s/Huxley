# Skill: spotify

Plays music, and audiobooks, from a household Spotify Premium account
on any Huxley device. Persona-agnostic; same `AudioStream` path as
[radio](./radio.md): Spotify's audio reaches the client through the
server's one audio channel, never through a separate speaker.

## What it does

| Tool            | Args                                                                   | Returns                                                                                                                         |
| --------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `play_music`    | `query?: string`, `kind?: any \| track \| artist \| album \| playlist` | `{ ok, playing, by, kind, alternatives? }` + `AudioStream`; no query: `{ ok, action: "resume" }` + `AudioStream`; or `{ ok: false, reason, say }` |
| `music_control` | `action: pause \| resume \| next \| previous`                         | pause: `CancelMedia`; the others: `AudioStream` (resume). In a book, next / previous are chapters |
| `whats_playing` | (none)                                                                | `{ ok, title, by, then }`, for a book `{ ok, book, by, chapter, then }` (info tool; what played resumes after the answer), or `nothing_playing` |
| `play_book`     | `title?`, `from_beginning?`, `confirmed?`                            | `{ ok, playing, by, kind: "audiobook" }` + `AudioStream`, or `{ ok: false, reason, then }` |
| `book_control`  | `action: back \| forward \| restart_chapter \| go_to_chapter \| go_back_to_saved_place`, `seconds?`, `chapter?` | `AudioStream` (resume), or `{ ok: false, reason, then }` |
| `find_books`    | `query?`                                                              | `{ ok, books: [{ n, title, by, length?, about?, listened_here }], then }` |

**Search returns up to three distinct candidates.** The best plays; the
other two go back as `alternatives` (`title`, `by`, `kind`). They are
not spoken (a stream tool gets no follow-up round) but stay in the
conversation, so after _"no, the other one"_ the model can pick one
instead of repeating the same guess. Repeats (same URI, or the same
title by the same artist) are skipped. A mixed (`any`) search puts the
best section first: a song beats an album beats a playlist beats an
artist, and an artist wins only when the query is its exact name.

**A song (`kind: track`) is matched by how its title sounds.** Spoken
English titles reach the tool garbled while the artist survives
("Weird Fishes de Radiohead" searched as "irfixes Radiohead"; Spotify
ranked "Let Down" first and Let Down played, 2026-10-12). The query
minus the artist's words is compared, letter by letter, with each
result's title (and each part of it, "Weird Fishes / Arpeggi"); when the
top title is far from it (under 0.5) and another is close (0.6 or more,
0.2 ahead), that one plays. If none of the top five is close, the
artist's own tracks are searched too. A clear title keeps Spotify's
order.

**No query** (the user asked for music without saying which): continue
what Soloist has loaded (it keeps its last context across restarts).
Paused: the same stream as `music_control(resume)`. Idle (it ran out; a
single song ends that way): start that context again by its URI, since
a bare `play` may have nothing to resume. If nothing was ever loaded,
it answers `nothing_to_resume` and the model asks which artist or song.
This is a deliberate persona-neutral choice: the skill never picks a
default genre or artist, because that would assume the user's taste.

The tool descriptions name no example artists or genres: when the model
misheard, it used them as its guess (2026-10-12: a request for one band
searched the example genre and played the example artist).

An artist plays as the artist (`spotify:artist:...`), like the play
button on an artist page: their popular songs, then more. Soloist's docs
list only track, album, playlist and episode URIs, but it plays artist
URIs (verified 2026-10-10). No queueing, so nothing leaks into the next
request.

Soloist's events, as observed (its docs don't show the shapes): a track
item's title is at `decorations.identity.name` and its artists at
`decorations.creators[].entity.decorations.identity.name`. After a
`play`, Soloist first reports `playing` for the _old_ context, then
`context_changed`, `track_changed`, `buffering`, `playing`; and a freshly
started device turns `is_active` only a moment after playback starts. So
a start counts only when the new context is playing, and a takeover only
when the device was active and then wasn't.

## Audiobooks (T1.39, T1.40, T1.45)

Built for the least technical listener: every request a person makes of a
book, and every way a book stops, answered in exact words (a stated fact
plus the line to say: told only to "tell the user Spotify wouldn't play
it", the model said the book was playing, 2026-10-12). Book logic lives in
`books.py`, the words in `lines.py`.

**How a book plays.** A book is a `spotify:show:` URI whose chapters are
episodes. Spotify keeps the account's place in each book, so asking for it
again continues it. Measured on Soloist (silently, 2026-10-12): in the
book, `skip_next` goes to the next chapter at 0:00; `skip_prev` at a
chapter's start goes back one, mid-chapter restarts it; a skip while
paused starts playback, a `seek` while paused stays paused. Playing a
chapter's own URI drops the book (it plays alone and stops), so chapters
are always reached by stepping inside the book, and the stream stays
silent until the target chapter is on. Chapters are matched by their name
("5" finds "Capítulo 5"), since short parts ("Créditos iniciales",
"Primera Parte") make the index drift.

**Requests.** `play_book(title)` searches books (reading guides dropped,
the persona's language first); a title far from what was heard, with no
author named, or one whose main words before the last heard one were never
said ("Hechos de los Apóstoles" for "Los doce apóstoles" heard as "Los dos
apos"; a title named by its opening words, "Cien años", is fine), is
confirmed first (`ask_which`), since a wrong book costs listening hours. A top result sharing no main title word with what was said is never asked about ("Mi alma se la dejo al diablo", not on Spotify CO, was asked back as "¿Es La melancolía de los feos?", 2026-10-14): a close result further down is taken, else "No encontré <título> en Spotify" (`book_not_found_title`); with the author named it is "No encontré ese libro en Spotify. De <autor> está <otro>. ¿Lo pongo?" (`book_other_by`), and "sí" plays that one. The tool texts ask for an author or title the model knows in its real spelling: Spotify's search found nothing for "Castro Caicedo" as transcribed, and his book for "Castro Caycedo". An `author` (said, or just looked up) searches "title author" and keeps only that author's books; none is "No encontré <título>, de <autor>, en Spotify" (`book_not_found_by`), never another author's book ("El divino" of Gardeazábal, not on Spotify CO, was offered as a self-help "El Amor Divino Revelado", 2026-10-14). The question names the title without its subtitle. No title: the last book. "El segundo" after
`find_books` is the second book read. `from_beginning` with no chapter
list (Spotify's `/audiobooks` blocked) says it can't go back to the start
right now (`cant_restart`) and the book goes on, rather than resuming and
claiming a restart. `from_beginning` past the second
chapter asks book or chapter (`ask_whole_book`), keeps the place it
leaves, and `book_control(go_back_to_saved_place)` undoes it; it is also
how to erase a book's progress. A finished book asks before starting
over. `music_control` next / previous move chapters (the ends are said);
`book_control` back / forward seek inside the chapter (30 s by default),
`restart_chapter`, `go_to_chapter`. `whats_playing` says the book, the
chapter and what's left of each. `find_books` offers books from the
curated shelf (see [The shelf](#the-shelf-t145)); a title said that is on
the shelf plays its checked edition without `ask_which`, and a kind of
book said to `play_book` ("pon algo de misterio") goes to the shelf
instead of search. A resumed book backs up 2 s plus a tenth of the pause
(at most 20 s) so it doesn't start mid-word. "Pon música" after a book
plays the last music, not the book (kept apart in storage).

**Hours.** Spotify gives a Premium Familiar plan's audiobook hours to its
holder only; the account the device plays as needs hours bought for it.
Not every book is included: some are purchase-only, and the Web API can't
tell which (with an app token every chapter says `payment_required`).
Spotify refuses a book it won't play: `playing`, then `idle` 0.3 to 1.2 s
later, no error. A fresh start idle within 3 s (`REFUSED_S`) is a
refusal, and why decides what he hears: with `book_hours_per_month` set,
a month's audio near the budget is the hours (`book_no_hours`); a book
that played here in the last 30 min means the hours are there, so this
book isn't available (`book_unavailable`: paid apart or not offered, the
same to the listener); a book known to play here (played, or a check's
"ok") is explained after its stream ends by trying another book known to
play (`spotify.refusal_explained control= control_result= cause=`): it
plays, so this book is unavailable; it's refused too, so the hours ran
out; no other book to try: the hours if this one really played here, else
unavailable. Anything else is unavailable. `start_refused` logs
`cause=checking` while the check runs: trust `refusal_explained` for
"no hours". (2026-10-12: Siddhartha had passed a too-quick check, was
refused, and the walkie said the month's hours were gone while three
other books played.) A refused book is remembered for 30 days and its
check's "ok" dropped, so it isn't offered again.

**Offers only include books that play.** `find_books` tries each
unknown candidate for a moment (up to 4; Spotify stops one it won't play
within 0.3-1.2 s: refused is "playing", the first chapter, "buffering",
idle; a check says "ok" only for a book still playing at the end of
1.6 s, since the first "playing" comes before Spotify's verdict; a refusal
ends it early), with nothing
reaching a speaker (Soloist's sink has no reader between streams), keeps
the first three that play, remembers both answers (a refusal for 30
days), and loads back what was there, paused (a book at its own place;
music restarts its album or list). If a candidate is refused and a book
known to play is refused too, the month's hours ran out (`no_hours`), and
nothing is filed as unavailable; that extra check runs only when nothing
in the round (or in the last 30 min) played. It never checks while a
stream plays. Measured in production (2026-10-13): one check about 2 s,
a first offer of three unchecked books about 6 s (the waiting sound plays
meanwhile); 0 refusals in about 20 checks. A persona without a shelf
keeps T1.40's open search: with no query, the books started here; with
one, a search.

**Seeks go after "play".** A seek sent before "play" lands after Soloist
has played about a second from its buffer (Mario heard the old spot, a
cut, then the new one): the resume back-up, back / forward and
restart_chapter seek once playing, and the stream stays silent until
Soloist reports the new spot (at most 2 s). The skill keeps which books played here (10 s of audio proves it),
which ended, each book's place, and the seconds of book audio per month.

**A place never runs ahead of what was sent.** Soloist plays on when
nobody reads its capture: a walkie switched off mid-book kept the stream
"playing" for 16 minutes before the server noticed, and the place jumped
16 minutes past what was heard (2026-10-12). Each chapter's audio is
anchored where it started (Soloist's position at the first chunk sent);
a place more than 5 s past the anchor plus the audio sent since is kept
at 10 s before the last audio sent instead, and at the stream's end
Soloist is sought back there too, since a plain resume starts from
Soloist's own position (`spotify.place_capped ahead_ms= kept_ms=`).

**When a book stops**, it is always said: idle in the last chapter, or
Spotify moving on to something else after it, is the end
(`book_finished`, and the book is remembered as finished); idle anywhere
else is a cut (`book_cut`); another device taking it, Spotify not
answering, a chapter that can't be reached and a long call each have a
line. Falling asleep: with no press for `book_sleep_minutes` (60), the
book pauses at the next chapter boundary, without a word. Reading speed
can't change (Soloist has no command for it).

## The shelf (T1.45)

Spotify's Web API gives an app like ours no recommendations, browse or
charts (cut in February 2026 for development-mode apps), and its search
answers a theme with study guides and obscure titles. So the skill
**only ever offers books from a curated shelf** of well-known books
([ADR 2026-10-13](../decisions.md#2026-10-13-book-recommendations-come-only-from-a-curated-shelf-t145));
search still plays a book or an author asked for by name.

**Files.** `server/personas/abuelos/books/shelf.yaml` is the source,
edited by hand: `categories` (spoken, in offer order) and `books`, each
with `title`, `author`, `category`, `tags`, `pitch` (one spoken sentence;
never the length), and optionally a pinned `uri` and the preferred
`narrator`. `shelf.json` next to it is written only by the build and read
only by the skill. Both are in the repo (famous books, not personal
data), deployed with the code. AI-voiced editions are treated like
human-narrated ones (Mario's call; Spotify has no field for it anyway).

**Build.** `huxley-spotify-shelf <persona_dir> [--write] [--only TITLE]`
(`shelf_build.py`) checks every book against Spotify in the persona's
`market`, with the Web API app in `data/secrets/spotify/values.json`, so
it runs where the secrets are (the Pi). It searches "title author", then
the title alone, and keeps an edition only if the title is close (0.75,
subtitle and edition note dropped, digits count: "1984"), an author's
surname matches, it is in the persona's language, it isn't a reading
guide or an abridgement (title, study-guide phrases at the start of the
synopsis, or Spotify's "Abridged edition" note), and Spotify lists its
chapters (the first edition that does; a pinned one must). A pinned `uri`
wins, then the preferred narrator, then the title score, then Spotify's
order; the other editions are printed. One line per book: `OK`, `CHECK`
(kept, look at the reason: no pinned uri yet, another narrator) or
`REJECT` (left out, why). Exit 1 on any reject, 2 when Spotify can't be
reached; the write is atomic; `--only` re-checks some titles and keeps
the rest. **Requests are rationed**: one `/audiobooks` request per
edition, none for an edition the previous `shelf.json` already has for
that book, and a run stopped by Spotify's limit writes what it checked
(the rest as before), so running again continues. On 2026-10-13 four
full runs in three hours got the app blocked on `/audiobooks` for about
19 hours (`Retry-After: 68658`; search stayed open): fix one book with
`--only`. Only the build retries a 429 (3 times, honouring
`Retry-After` up to 2 minutes); the live skill never waits on one, and
both remember Spotify's `Retry-After` per part of the API (`/audiobooks`
apart from `/search`) and fail at once until then instead of asking
again. `--search-only` builds without any `/audiobooks` request (while
Spotify holds it): editions are chosen from searches, a pinned edition
must come back from one, and each book is written `chapters_checked:
false`; the next normal run looks those up (one request each) and keeps
the ones already checked.
During such a block, books still play and their listening still counts
(the month's seconds, played, the last book: the stream is a book by its
`spotify:show:` link), but chapter commands, the end-of-book line and the
place don't work (`spotify.book_info_failed`, then
`spotify.book_without_chapters`; `stream_ended book_info=false`). Before
2026-10-14 such a book was treated like music and not counted.

**Loading.** The skill reads `shelf.json` again whenever its mtime or size
changes (checked at each `find_books` / `play_book`). An invalid file
keeps the last good shelf; a missing one turns offers back into open
search.

**`find_books(query?, like?, more?, listened?)`** never offers from open
search:

- no arguments ("recomiéndame un libro", "¿qué libros tienes?"): the
  next three of the shelf in rotation order, one kind after another
  (`shelf.rotation_order`), continuing after the last three offered
  (`book:rotation:last`) and round again at the end; never one he
  started, finished, disliked or Spotify refused. Mario, 2026-10-14:
  choosing a kind first was too much for him (it used to name up to four
  categories and his book in progress);
- `query`: a shelf title; else, when every word names a kind of book on
  the shelf, a category or tag ("Breve historia del tiempo" is a title,
  not the history category); else a shelf author; best three. A book or
  author named that isn't on the shelf searches Spotify, keeping only
  that book (every edition gets its chance) or books by that author, never
  one he finished, didn't like, left or that was refused;
- `like`: books sharing category, tags and author with one he names; a
  book not on the shelf is looked up once for its author and synopsis
  words;
- `more`: the next three of the last offer ("otros"; after a
  recommendation, the next three of the rotation); after a single book,
  books like it; with a new subject, a fresh offer for it. A query with
  "otros", "más" or "adicionales" naming the last offer's subject is
  `more` too ("Julio Verne títulos adicionales" re-read the same three,
  2026-10-14); subjects are compared as the shelf resolves them;
- `listened`: the books started here.

Asking for books ends whatever was playing (`CancelMedia`): it stays
paused at its place instead of coming back over the offer (2026-10-14:
after each list the last book resumed, "¿Cuál pongo?" included); "sigue
con el libro" continues it. A book in general ("pon un libro", "ponme
algo para leer") is choosing, not continuing: the tool text sends it
to `find_books` with no arguments, which recommends the next three.

The skill writes the offer itself in `then` ("Te recomiendo tres. Uno,
...", or for a topic "De misterio tengo tres.
Uno, X, de Y: <pitch>. ... ¿Cuál pongo, o busco otros?"; when the subject
has more books left, never a count that reads as the total: "De Julio
Verne tengo varios; te digo tres.") and returns
`books[{n, title, by, about}]` so "el segundo" or "el dos" plays the
second; a book he named and already started is offered in his own
edition, so "ponlo" continues it. Nothing
fits: "En la repisa no tengo libros de eso. ¿Te recomiendo otros?"; a
topic run out: "Ya no tengo más de <tema>. ¿Te recomiendo otros?".

**Ranking.** Never offered: finished (at any time, kept as
`book:ended:<id>`), disliked, refused here, started (in progress, or
abandoned: not heard for 21 days, never finished, not the last book). Another
edition of a book in memory counts as that book (same author, close
title), and `play_book` of a shelf title continues his own edition if he
started one. Then liked author +2, finished author +1, liked category +1,
disliked author -3; ties go to the book offered least recently
(`book:offered:<id>`), then shelf order.

**`book_opinion(opinion, title?)`**: "me gustó", "no me gustó este libro"
(liked / disliked; no title: the book on, or the last). Kept as
`book:opinion:<id>`; the skill says "Anotado que <título> gustó...".

**The page's view of each book.** Whenever a book's place, finished state
or opinion changes, the skill writes `book:summary:<id>` (`<id>` is the
last part of the `spotify:show:` URI), so huxley-watch reads the settings
key `spotify:book:summary:<id>`, whose value is JSON text:
`{uri, title, author, chapters, chapter (0-based), position_ms,
last_at (epoch s), finished, opinion ("liked" | "disliked" | null),
source ("shelf" | "search")}`. Writes are serialized (a place and a book's
end land together) and books heard before T1.45 get one at setup.

## How it works

```
Spotify app ─┐                        ┌─ WebSocket (control, events) ─┐
             └► Spotify Soloist ──────┤                               ├─ skill ─► AudioStream ─► client
                (Connect device)      └─ null sink monitor ─► TCP PCM ┘
```

- **Engine: [Spotify Soloist](https://developer.spotify.com/documentation/soloist)**,
  Spotify's official headless Spotify Connect client (Linux only).
  librespot-based players don't work for Premium accounts created since
  December 2024: Spotify refuses their audio keys (see T1.28 in
  `docs/triage.md`).
- **Control:** Soloist's local WebSocket (`soloist.py`): one reconnecting
  connection, a cache of auth and playback state, and an ordered command
  queue. A stream's `finally` queues a `pause` without awaiting it (it
  runs in the coordinator's cancel path); commands from a replaced
  stream (an older generation) are dropped.
- **Audio:** Soloist plays into a PulseAudio null sink whose monitor is
  served over TCP as PCM16 mono 24 kHz (`capture.py`). Each stream opens
  a fresh connection, so nothing stale plays after a pause, and the
  sink's clock paces it.
- **Search:** Spotify Web API with the app's own token
  (client-credentials, renewed hourly). Development mode: the app owner
  must be Premium, at most 5 users, 10 results per search. `market` must
  be set; app tokens can't use `from_token`.
- **Every press pauses the music** (the framework holds the stream: the
  factory's `finally` queues a Soloist pause) and the turn's end resumes
  it by calling the factory again, which sends a bare `play`
  ([ADR 2026-10-12](../decisions.md#2026-10-12-a-press-pauses-content-the-turns-end-resumes-it-t130)). So "pause" must return `CancelMedia` (the
  stream ends, nothing comes back), `next` and `previous` act and then
  return a resume stream that replaces the held one (a song's `previous`
  first seeks to 0 and waits for it, because past a song's first seconds
  Soloist's `skip_prev` only restarts it, as Spotify's own button does:
  `spotify.previous` with `position_ms`, `rewound`, `landed`), and `whats_playing`
  is a plain info tool: a tool with an `AudioStream` gets no follow-up
  round, so its answer would never be spoken (seen live). Its `then`
  names the exact line to say; the music comes back after it. (Before
  2026-10-12 a press stopped the music and `whats_playing` queued an
  `inject_turn` to resume it.)
- **Content type:** `NONMIXABLE` with `patience` (default 30 min): a
  reminder or call pauses the music and it comes back afterwards.
  (Ducking would interleave music and speech; see T1.29.)
- **Volume** stays with the client: every play pins Soloist at 100, so a
  Spotify app on a phone can't silently turn the device down.
- **Loudness:** Soloist has no normalization option, and masters vary
  by track and are loud, so streams opt in to the framework's leveler
  (`loudness_key`: `spotify:book:<uri>` for a book, so a narrator's
  level holds from one session to the next; `spotify:music` for the
  rest). It plays near -20 LUFS, like the voice
  ([`sounds.md` "Loudness"](../sounds.md#loudness)); `audio.level` log
  lines say what Spotify actually measured.

## Failures (every one is spoken)

| Situation                                     | What happens                                                                                  |
| --------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Soloist unreachable                           | `play_music` returns `player_offline`; the model offers radio                                 |
| Soloist not paired                            | `player_not_paired`; offers radio                                                             |
| Web API app missing or failing                | `search_not_configured` / `search_failed`; offers radio                                       |
| Nothing found                                 | `not_found`; asks for another song or artist                                                  |
| No query and nothing ever played              | `nothing_to_resume`; asks which artist or song                                                |
| Soloist doesn't start playing within 10 s     | stream ends, narrated `start_failed`                                                          |
| Someone picks another device in a Spotify app | stream ends, narrated `takeover` (no pause sent)                                              |
| The audio feed drops mid-song                 | stream ends, narrated `interrupted`                                                           |
| The audio feed closes before any sound        | narrated `player_offline` (Docker accepts the port even when the sound server inside is down) |
| Paused from a Spotify app for 3 s             | stream ends quietly, freeing the speaker                                                      |
| A call outlasts the 30 min patience           | narrated `patience_expired` (the music stays paused)                                          |
| Album / playlist runs out                     | stream ends quietly                                                                           |
| Idle within 3 s of a play (refused)           | a book: `book_no_hours` only when the budget is spent or another known book is refused too (`refusal_explained`), else `book_unavailable`; music: `start_failed` |
| A book stops, is taken, can't be reached      | `book_finished`, `book_cut`, `book_moved`, `book_offline`, `book_nav_failed`, `book_patience` (see Audiobooks) |

"What song is this?", like any question, gets the music back after the
answer. Not after "pausa" (`CancelMedia`), and not after it moved to
another device (the stream ended on its own and is never restarted).

## Configuration

Persona's `skills.spotify` block (all optional):

| Key                | Default               | Notes                                                  |
| ------------------ | --------------------- | ------------------------------------------------------ |
| `soloist_ws`       | `ws://127.0.0.1:9090` | Soloist's WebSocket API (`--ws`)                       |
| `pcm`              | `127.0.0.1:4712`      | `host:port` of the PCM capture                         |
| `market`           | (none)                | ISO country for search; set it so results are playable |
| `start_sound`      | (none)                | Earcon role before the music (first start only)        |
| `sounds_path`      | `sounds`              | Earcon directory, relative to the persona data dir     |
| `patience_minutes` | `30`                  | How long paused music waits to come back               |
| `book_sleep_minutes` | `60`                | No press this long: a book pauses at the next chapter (0: off) |
| `book_hours_per_month` | (none)            | The device account's audiobook hours, if known: a refusal near it is "hours" |
| `book_shelf`       | `../books/shelf.json` | The built shelf, relative to the persona data dir (absolute allowed); missing: open search |

Secrets, in `<persona data>/secrets/spotify/values.json` (gitignored):
`client_id`, `client_secret` (Web API app, for search) and
`soloist_api_key` (read by the dev scripts; on Linux it belongs in the
Soloist unit's environment).

## Setup

The step-by-step guide for any user connecting their own account is on
the docs site: [Connect Spotify](../../web/content/docs/use/connect-spotify.mdx).
In short:

1. With the **Premium** account that will play, on
   [developer.spotify.com](https://developer.spotify.com/dashboard):
   generate a Soloist API key (Dashboard → Soloist), and create a Web
   API app (redirect URI `http://127.0.0.1:8888/callback`) for its client
   id and secret. Put all three in `values.json`.
2. Run Soloist with a null sink and the TCP capture.
   - **Mac (development):** `server/skills/spotify/dev/` has a container
     that does all of it. Pair once with `pair-on-mac.sh` (it announces
     the device from the Mac, since a container can't join the local
     network's discovery), then `run.sh`. The container downloads the
     current Soloist build from Spotify at each start (builds expire after
     90 days; Spotify forbids redistributing them, so they never go in the
     image or the repo).
   - **Linux (production):** the same as `dev/entrypoint.sh`, as a
     systemd user unit next to the server; pair once with `--pair`.
     (Grandpa's Pi runs the dev container itself, `run.sh` from the
     deployed checkout.)
   - Soloist tries one login at start and never again. The entrypoint
     waits until Spotify answers before starting it (the Pi boots before
     its network after a power cut), and ends the container on "login
     failed", so Docker's restart policy tries again 30 s later: after the
     2026-10-14 power cut it sat logged out for three days.
3. Add `spotify:` to the persona's `skills` block and restart the server.

## Logs

`spotify.setup_complete`, `spotify.ws_state` (connected / retrying),
`spotify.auth_state` (logged in, active; every answer, so "logged out"
from the start shows too), `spotify.not_logged_in` (a warning: connected
but logged out 2 minutes, then every 30 minutes; the watch alerts on it),
`spotify.playback` (status,
context), `spotify.track`, `spotify.play_requested` (query, the kind asked and
the kind matched, match, uri, `alternatives`), `spotify.play_last`
(context and `restart` for a request with no query),
`spotify.play_started` (`latency_ms`, context; for a book `book`,
`chapter`, `chapter_index` from 0 and `chapters`), `spotify.start_failed`,
`spotify.stream_ended` (`reason`: cancelled, finished, takeover,
paused_elsewhere, capture_closed, start_failed, player_offline; for a book
`audio_ms`, the chapter it ended on, as at the start, and `book_info`:
false when Spotify gave no chapters), `spotify.book_without_chapters`
(uri, title: a book stream counted from its link),
`spotify.book_nav` (the book and the chapter reached, as at the start),
`spotify.capture_closed` (`chunks`), `spotify.soloist_error`,
`spotify.search_failed`, `spotify.capture_failed`, `spotify.unavailable`,
`spotify.control`, `spotify.previous`, `spotify.book_requested` (`source`:
list, shelf, search or last), `spotify.book_probe`, `spotify.books_checked`.
The shelf: `spotify.shelf_loaded` (path, books, categories, skipped),
`spotify.shelf_invalid` (error, kept), `spotify.shelf_missing`,
`spotify.shelf_browse` (mode, query, like, like_source, excluded,
offered, offset), `spotify.shelf_nothing` (reason: nothing_left,
all_excluded, like_nothing, no_more, none_playable, no_match,
like_unknown),
`spotify.book_redirected` (query), `spotify.book_opinion` (title,
opinion, source), `spotify.shelf_blocked` (reason: Spotify unavailable
before an offer), `spotify.shelf_more_new_subject` (query, like, the last
ones).

The Soloist build's expiry is logged by the container at start ("client
expires in N days"); an expired build exits with code 10, which the skill
sees as `player_offline`.
