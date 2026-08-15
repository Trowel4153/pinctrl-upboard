# debian/changelog does not parse: `debuild` fails, `dpkg-parsechangelog` warns on every entry

**Labels:** bug, packaging

## Summary

`debian/changelog` has a malformed trailer line in the 1.1.9 entry, no blank
line between any two entries, and an impossible date in 1.1.0. The first of
those stops `dpkg-buildpackage` outright.

Unlike the other three reports this one is verified end to end — no hardware is
involved, and the fix makes `dpkg-parsechangelog --all` run clean.

## Affected

| | |
|---|---|
| File | `debian/changelog` |
| Symptom | `debuild` / `dpkg-buildpackage` refuses to build the package |

## What happens

```
$ debuild -i -us -uc
debian/changelog(16): badly formatted trailer line
dpkg-buildpackage: error: cannot parse maintainer email address ""
    from changelog entry
```

The 1.1.9 trailer has the timezone jammed against the seconds:

```
 -- Gary Wang <garywang@aaeon.com.tw>  Tue, 10 Mar 2026 17:39:45+0100
```

`17:39:45+0100` is not an RFC 2822 date, so the whole trailer fails to parse and
the maintainer field comes back empty — hence the second error, which is a
consequence of the first rather than a separate problem.

Two more issues surface once that one is fixed:

- **No blank line between entries.** Every trailer runs straight into the next
  `pinctrl-upboard (x.y.z) stretch; urgency=medium` line, producing a pair of
  warnings per entry: `found start of entry where expected more change data or
  trailer`. Roughly 40 warnings across the file.
- **1.1.0 is dated `Fri, 60 Jun 2023`.** June has 30 days.

## Reproduce

```bash
dpkg-parsechangelog -l debian/changelog --all
```

## Suggested fix

1. `17:39:45+0100` → `17:39:45 +0100`, and drop the trailing whitespace on that
   line.
2. Insert a blank line before each `pinctrl-upboard (...)` header.
3. `Fri, 60 Jun 2023` → `Fri, 30 Jun 2023`. 30 June 2023 was in fact a Friday,
   and it sits correctly between the surrounding 1.0.8 (22 May 2023) and 1.1.1
   (21 Aug 2023) entries.

After that, `dpkg-parsechangelog -l debian/changelog --all` emits no warnings
and `debuild -i -us -uc` reaches a built `.deb`.

The change is whitespace and two characters; no entry text is reworded and no
version or maintainer is altered.
