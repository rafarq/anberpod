# RSS import file

Place up to 100 public `http://` or `https://` feed URLs, one per line, in
`data/imports/rss_urls.txt`. Empty lines and lines beginning with `#` are
ignored. URLs longer than 2048 characters, URLs containing credentials, and
unsupported schemes are rejected independently. HTTP entries are visibly
marked as unencrypted. Feeds must be public and unauthenticated: local,
private, reserved, link-local, and otherwise non-public addresses are blocked
before a connection and after every redirect.

Choose **Settings > Import RSS file** to perform the network fetch. Import is
never automatic at startup. AnberPod applies bounded connect/read/total
timeouts, at most five redirects, and a 5 MiB decompressed XML limit. HTTPS
cannot redirect to HTTP. RSS 2.0 and Atom are supported; DTDs and entity
declarations are rejected.

The preview displays a result for each processed line. Confirming a successful
preview stores the podcast and episodes and subscribes to it. Duplicate feeds
open the saved podcast without another request. Results are atomically written
to `data/imports/rss_urls.result.txt` as `OK`, `DUPLICATE`, or a safe error
code; `rss_urls.txt` is retained unchanged.

Updates happen only when **Update now** is selected. They use ETag and
Last-Modified when available. Unsubscribing removes only subscription
membership; saved podcast metadata, episodes, playback progress, and download
records remain available for a later resubscription.
