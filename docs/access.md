# Who can use Kura

The person whose vault it is is the **owner**. Who else gets in is up to you. Pick one of these; the settings they name
are all in [settings](settings.md).

## You, on localhost

`KURA_AUTH=open` with `KURA_BIND=127.0.0.1`, as in the [Quickstart](../README.md#quickstart). There's no sign-in, so
Kura answers only to an IP address, `localhost`, `KURA_PUBLIC_URL`'s name and `KURA_ALLOWED_HOSTS`.

## People on your tailnet

Listen on `127.0.0.1`, put `tailscale serve` in front, and list their Tailscale logins in `KURA_USERS`
(`KURA_AUTH=tailscale`, the default; `*` = anyone, unset = nobody). Kura trusts the `Tailscale-User-Login` header that
Serve sets, so block the port from outside: nothing may reach Kura around Serve.

## People, agents, sign-in and Shiori devices

Turn on Machiya's identity file:

```sh
cd app && python3 -m vaultkit.identity setup
```

From the Quickstart's venv, that's `cd app && ../.venv/bin/python -m vaultkit.identity setup` (vaultkit lives in
`app/`). It prints the settings for each app. It's off unless you set it; see
[Machiya's identity guide](https://github.com/machiya-kobo/machiya/blob/main/docs/identity.md).

Kura's identity settings: `MACHIYA_IDENTITY_FILE`, `KURA_SIGNIN`, `KURA_AUTH_HEADER`, `KURA_BIND_BEHIND_PROXY`,
`KURA_ACCEPT_APP_CAPS` and `KURA_PUBLIC_URL`.

## Hister's users as the sign-in

`KURA_AUTH=hister` makes a Hister account the sign-in. It's off unless you set it, and it doesn't combine with the
identity file.

- Kura asks Machiya's sign-in helper (hister-login) whether you're signed in to Hister. One sign-in covers Hister and
  every app, and signing out anywhere ends it.
- Signed out, a page goes to the helper's sign-in and an API call gets `401 {"error": "sign in", "signin": …}`.
- A Hister account that isn't in `KURA_HISTER_USERS` gets 403.
- With the helper or Hister unreachable, every page and call is a 503 (`KURA_AUTH_FALLBACK=none`, the default). With
  `KURA_AUTH_FALLBACK=tailscale`, a Tailscale login listed in `KURA_USERS` gets in instead, with a banner saying sign-in is
  unavailable. That works only while nobody answers: a signed-out visitor still goes to the sign-in, and a login that
  isn't listed gets 403.
- Each app keeps a host-only cookie of its own, from a one-time code the helper hands over. (The shared cookie is on
  its way out.)
- A caller that isn't a browser sends a room token (`Authorization: Bearer mht_…`, minted on the helper, good only for
  the apps it names) in place of Hister's own token.

See [Machiya's identity guide](https://github.com/machiya-kobo/machiya/blob/main/docs/identity.md#hister-sign-in-authhister).

## Always open

`/api/status` answers everyone, for monitoring, and so does `/api/changelog` (the first 64 KiB of
[`app/CHANGELOG.md`](../app/CHANGELOG.md), for the Machiya landing page's recent deploys). Anyone but the owner gets
`/api/status` without the repository URL, the folder or error details.
