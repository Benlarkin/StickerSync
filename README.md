# StickerSync

Sync the stickers you've favorited on TikTok to your Discord server as real
server stickers. It converts them to APNG or GIF as needed and squeezes them
under Discord's limits automatically.

Run it whenever you want a fresh batch. It remembers what it has already
uploaded, so each run only handles your new favorites.

```
$ stickersync run
Found 42 favorited sticker(s).
ok cat dance: apng/step1/fullc, 38 frame(s), 211 KB
ok tt_9c41e2aa: gif/step2/128c, 24 frame(s), 487 KB
^ uploaded cat dance
^ uploaded tt_9c41e2aa

Done. new: 2, converted: 2, uploaded: 2, removed: 0, failed: 0, waiting for slots: 0
```

## How it works

1. **Capture**: StickerSync opens a normal Chromium window with its own saved
   profile. You log in to TikTok once, and later runs reuse that session. While
   you open the sticker picker's **Favorites** tab, it records the API responses
   the page loads. TikTok's request signing is handled by the real browser, so
   StickerSync doesn't depend on reverse-engineered endpoints.
2. **Parse**: It finds sticker records in those responses by looking for
   TikTok's image structure (`{"uri": ..., "url_list": [...]}`) rather than one
   exact response shape. Avatars and video covers are ignored.
3. **Convert**: Each sticker is scaled to exactly 320×320 with transparent
   padding, trimmed to 5 seconds, and run down a compression ladder until it
   fits in 512 KB. The ladder reduces colors first and drops frames only when it
   has to. APNG is preferred because it keeps smooth semi-transparent edges, and
   GIF is the fallback.
4. **Upload**: It uploads through Discord's REST API with your bot, keeps track
   of what was synced, and can rotate out its own oldest stickers when the
   server runs out of slots.

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/Benlarkin/StickerSync && cd StickerSync
pip install -e ".[browser]"      # or: pipx install ".[browser]"
playwright install chromium
cp .env.example .env             # then fill it in
```

### Discord bot

1. Create an application at <https://discord.com/developers/applications> and
   add a **Bot**. Copy its token into `DISCORD_BOT_TOKEN`.
2. Invite it to your server with the **Manage Expressions** permission. In the
   OAuth2 URL generator, choose scope `bot` and permission *Manage Expressions*.
3. Turn on Developer Mode in Discord, right-click your server, choose
   *Copy Server ID*, and put it in `DISCORD_GUILD_ID`.

### TikTok

```bash
stickersync login
```

Log in in the window that opens, then press Enter in the terminal.

## Usage

| Command | What it does |
| --- | --- |
| `stickersync run` | Capture favorites, convert new ones, upload. |
| `stickersync run --dry-run` | Same, but stops after converting. Look in `~/.stickersync/cache/converted/` to preview the files. |
| `stickersync run --pick` | Choose which new stickers to sync from a list. |
| `stickersync run --rotate` | When slots are full, delete StickerSync's **oldest** uploads to make room. Stickers you added by hand are never touched. |
| `stickersync run --limit 5` | Process at most 5 new stickers. |
| `stickersync run --format gif` | Force GIF (`auto` / `apng` / `gif`). |
| `stickersync run --from FILE` | Skip the browser and use a saved capture, a DevTools HAR export, a raw JSON response, or a text file of image URLs. |
| `stickersync capture` | Only record the favorites list and save it to `~/.stickersync/captures/`. |
| `stickersync inspect FILE` | Show which stickers a file contains and whether each one is new. |
| `stickersync status` | Show the local history and how many slots are used and free. |
| `stickersync prune --keep 10` | Delete StickerSync's oldest uploads, keeping 10. |

### Sticker slots

Discord servers get **5** sticker slots by default, rising to **15 / 30 / 60**
at boost levels 1 / 2 / 3. Stickers that don't fit are left pending and retried
on the next run. Use `--rotate` or `prune` to cycle in new ones.

### Where things are stored

Everything StickerSync writes goes under `~/.stickersync/`, which you can
change with `STICKERSYNC_HOME`. None of it goes in the repo:

- `browser-profile/` holds your TikTok session. Treat it like a password.
- `state.json` records which stickers have been uploaded, failed, or skipped.
- `captures/` holds the recorded API responses.
- `cache/` holds the original downloads and the converted files.

## Troubleshooting

**"No stickers recognized" after a capture.** TikTok may have moved the
endpoint. Run `stickersync capture --all`, open the capture file, and look
through `json_urls_seen` for the request that loads your favorites. Pass a
matching pattern with `--match`, or open an issue with the URL. Please leave
out cookies and tokens.

**The web app doesn't show favorite stickers.** Capture the traffic from the
mobile app instead (for example with HTTP Toolkit or mitmproxy), export it as
HAR, and run `stickersync run --from capture.har`.

**Downloads fail with 403.** TikTok CDN links expire, so capture again for
fresh URLs.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

The tests don't need network access or a browser. The converter tests generate
their own sample animations.

### Roadmap / ideas

- [ ] Click through the sticker picker automatically (fully hands-free capture)
- [ ] Binary-search palette size and frame step instead of a fixed ladder
- [ ] Suggest the sticker's emoji tag from its name
- [ ] Lottie passthrough for vector stickers
- [ ] Optional sync to custom emoji (256 KB limit) as well as stickers
- [ ] Preview grid (HTML) for `--dry-run`

## Disclaimer

This is a personal tool for moving your own favorites around. It isn't
affiliated with TikTok or Discord. Respect the creators of the stickers you
sync and both platforms' terms.

## License

MIT
