# ADR 0005 - Computer control is built in tiers

Status: accepted

A model that can "do anything on the PC" is exactly the target of prompt injection. Control is added
in tiers (see docs/desktop.md), each with its own rule, instead of one all-powerful tool.

Tier 1 (implemented): launch an installed program, open an http(s) URL, open a standard folder,
media keys. Programs come only from the operating system's own list and are matched against it;
there is no way to pass a command line. URLs are limited to http/https without embedded
credentials. These tools are WRITE_LOCAL: automatic in a clean run, approval required once untrusted
content (email, web) has entered the run.

Nothing in tiers 2-4 (window control, keyboard and mouse, commands, file changes) exists yet; each
will need its own approval design before being added.
