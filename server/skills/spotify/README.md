# huxley-skill-spotify

Plays music from a Spotify Premium account on any Huxley device, through
[Spotify Soloist](https://developer.spotify.com/documentation/soloist),
Spotify's official headless Connect client. Soloist runs beside the
server; the skill controls it over its local WebSocket and streams its
audio to the client like the radio skill does.

Tools: `play_music(query, kind?)`, `music_control(action)`,
`whats_playing()`; audiobooks: `play_book`, `book_control`,
`find_books` (offers from the persona's curated shelf, built with the
`huxley-spotify-shelf` script) and `book_opinion`. Full guide (setup,
pairing, the shelf, failure table):
[`docs/skills/spotify.md`](../../../docs/skills/spotify.md).

Needs: a Premium account, a Soloist API key and a Web API app (client id
and secret, for search), and a Linux host for Soloist (`dev/` runs it in
a container on a Mac).
