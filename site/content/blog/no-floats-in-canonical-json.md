---
title: We banned floats from our audit records, and the hashing bug class went with them
seo_title: Why we banned floats from canonical JSON
description: RFC 8785's hardest requirement is ECMAScript number formatting. Get it subtly wrong and hashes verify on one implementation and fail on another.
published: 2026-09-22
tags: [hashing, json, canonicalization]
---

Every record in our ledger carries a SHA-256 of its own contents, and each commits to the hash
of the record before it. The whole tamper-evidence argument rests on one assumption:

> Two independent implementations, given the same record, derive the same bytes.

If that ever fails, the failure is quiet and catastrophic. An auditor runs their own verifier
against an untouched ledger, gets a mismatch, and now has a genuine integrity finding caused
entirely by a serialization disagreement. You cannot argue your way out of that in a room.

So canonicalization is not a detail. It is the foundation, and it deserves to be narrower than
"`json.dumps` with `sort_keys=True`".

## What RFC 8785 asks for

[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785), the JSON Canonicalization Scheme, specifies
exactly one serialization for any JSON value: keys sorted by UTF-16 code unit, no insignificant
whitespace, a fixed escaping scheme, UTF-8 output.

Most of that is easy. `sort_keys=True`, `separators=(',', ':')`, `ensure_ascii=False`, and you
are most of the way there.

Then you reach numbers.

JCS requires numbers to be serialized exactly as **ECMAScript's `Number.prototype.toString`**
would render them. That algorithm produces the shortest decimal string that round-trips to the
same IEEE-754 double. It is a genuinely intricate piece of work — the shortest-round-trip
problem is the subject of a small literature (Steele & White's Dragon4, then Grisu, then Ryū),
and it has these edges:

- `1e21` renders as `1e+21`, but `1e20` renders as `100000000000000000000`. The threshold is
  exact and easy to get wrong by one.
- `-0` renders as `0`. A sign that survives your arithmetic silently vanishes in serialization.
- `0.1 + 0.2` renders as `0.30000000000000004`, not `0.3`.
- Python's `repr()` for floats *also* produces shortest-round-trip, and agrees with ECMAScript
  in the common range — which is worse than disagreeing loudly, because it means your tests
  pass and the divergence waits for an unusual value.

You can implement this correctly. Several libraries have. But the cost of being subtly wrong is
not "a bug" — it is an integrity failure that appears months later, in front of the one audience
you built the product for, with no way to tell a serialization disagreement from real tampering.

## We did not need floats

We looked at what our schema actually contains. Every numeric field is a count or a measurement
in integral units:

- `duration_ms` — an integer number of milliseconds
- `size_bytes` — an integer
- `seq` — an integer

There was no field that required a float, and no plausible one. Latency in milliseconds is an
integer. A byte count is an integer. Nothing in an audit record is naturally continuous.

So we removed the possibility:

```python
def _check(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise CanonicalizationError(
            f"float at {path}: floats are not permitted in ledger records because "
            f"their canonical form is implementation-sensitive; use an integer "
            f"(e.g. duration_ms, size_bytes) or a string"
        )
```

A float reaching the hasher is a schema violation and is raised as one, with the path to the
offending field so the caller knows exactly where it came from:

```
CanonicalizationError: float at $.outcome.duration_ms: floats are not permitted...
```

The rest of canonicalization is then twelve lines, and every one of them is obviously correct:

```python
return json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
```

## What this buys

The interoperability surface collapses. Any implementation in any language that can sort keys
and emit compact UTF-8 JSON will agree with ours, byte for byte, on every record we will ever
write. An auditor writing a verifier in Go does not need our library, or our test suite, or to
trust our reading of the RFC. They need `encoding/json` and forty minutes.

That is worth more than the flexibility we gave up, which was zero.

## When to reach for this move

The pattern generalises: **when a specification's hardest requirement is one you can arrange not
to need, arrange not to need it.**

It applies when the cost of being subtly wrong is silent and severe, when the difficult case is
separable from the rest, and when your domain genuinely does not require it. All three held
here. If you are hashing arbitrary user-supplied JSON, none of them do, and you should implement
JCS properly or use a library that has.

We also kept the door open. The check is one function, and the restriction is documented at the
top of the module with the reasoning. If a field ever genuinely needs a float, the conversation
starts with "here is why we banned these" rather than with someone discovering the constraint by
tripping over it.

Constraints you chose, with the reasoning written down, are cheap. Constraints you inherited
without knowing why are the expensive kind.
