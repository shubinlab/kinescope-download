# Playlist status and live-versus-recorded events

Do not infer whether an event was originally live or prerecorded from the page, chat, viewer count, or duration. Use an explicit player status if available.

If a media playlist can be inspected locally without exposing its signed URL, `#EXT-X-PLAYLIST-TYPE:VOD` declares a VOD playlist and `#EXT-X-ENDLIST` means no additional segments will be added. These markers describe the playlist, not whether the program was originally prerecorded or broadcast live. If the playlist is unavailable, unsafe to inspect, or lacks clear status, report the live/recording origin as unknown.
