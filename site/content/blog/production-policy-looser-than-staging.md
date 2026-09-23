---
title: Your production policy is probably looser than your staging policy
seo_title: Your prod policy is looser than your staging policy
description: A missing condition made our production rule permit what staging refused. Absent lines are the hardest thing to catch in a policy review.
published: 2026-09-21
tags: [policy, authorization, agents]
---

Our agent policy is a YAML document, and every action an agent takes records the specific rule
that permitted it. Not "RBAC allowed it" — a named rule, plus the SHA-256 of the policy text in
force at the time, so editing the policy later cannot retroactively change what an action was
permitted by.

The staging rule was carefully scoped:

```yaml
- id: rule:human_approved_reversible_rollback
  applies_to: [execute, rollback]
  requires_approval: true
  conditions:
    environment: [dev, staging]
    change_type: [rollback_deploy, restart_workload, scale_resource, config_change]
    reversible: true
    blast_radius_at_most: single_service
```

Four change types, explicitly listed. Reversible only. Bounded blast radius. Approval required.

The production rule looked stricter, because it said so:

```yaml
- id: rule:human_approved_production_change
  applies_to: [execute, rollback]
  requires_approval: true
  approver_roles: [sre-oncall]     # staging had no role requirement
  conditions:
    environment: [prod]
    reversible: true
    blast_radius_at_most: single_service
```

It adds a role requirement. It reads as the tighter of the two. Read them side by side and you
will probably agree.

## What a test found

We wrote a test asserting that an agent could not execute an arbitrary code change in
production. It failed:

```
assert not evaluate(action_type="execute", environment="prod",
                    change_type="code_change", reversible=True,
                    blast_radius="single_service").allowed
E   assert not True
E    +  where True = Decision(allowed=True,
E         rule_id='rule:human_approved_production_change', ...)
```

The production rule has no `change_type` condition. Staging enumerates four permitted kinds of
change; production enumerates none, and a condition that is absent does not constrain anything.

So the policy permitted an agent to ship a **code change to production** on a single approval —
while refusing that same change in staging, where `code_change` is not in the list.

## Why it reads as stricter

Every difference you can see between the two rules makes production tighter: the added
`approver_roles`, the narrowed `environment`. The thing that makes it looser is a line that
**isn't there**, and absent lines are not something you notice while reading.

This is the same shape as a firewall rule that is wide open because the port restriction was
never added, or an IAM policy that grants `*` because someone deleted the action list during a
debugging session and never restored it. The review looks at what is present.

There is a second reason it slipped past us: we wrote the staging rule first, got it right, and
then wrote the production rule by *analogy* rather than by derivation. Writing it second made it
feel like a refinement. It was a separate, weaker rule that happened to sit lower in the file.

## The structural fix

Two changes. First, name the change kinds explicitly, and say in the rule's own description why:

```yaml
- id: rule:human_approved_production_change
  description: >-
    A reversible production change of an operational kind -- rolling back a
    deployment, restarting a workload, scaling a resource or changing
    configuration -- requires approval from a human holding the sre-oncall role.
    The change kinds are enumerated deliberately: production must never be
    permitted a category of change that staging is not, and in particular an
    agent may not ship a code change to production on a single approval.
  conditions:
    environment: [prod]
    change_type: [rollback_deploy, restart_workload, scale_resource, config_change]
    reversible: true
    blast_radius_at_most: single_service
```

The invariant now lives in the file, next to the rule, where the next person to edit it will
read it.

Second — and this is the part worth stealing — **the policy engine fails closed on missing
facts.** A condition whose fact is `None` does not match:

```python
actual = facts.get(key)
if actual is None:
    # If we cannot tell whether a change is reversible,
    # we must not treat it as reversible.
    return False
```

Combined with a default-deny fallthrough and explicit denies short-circuiting matching allows,
this means a *missing fact* is safe. It does not, however, save you from a *missing condition* —
that is the hole we hit, and no amount of runtime strictness closes it. Only an assertion does.

## The test that should exist in your policy suite

Whatever your policy language, this property is worth asserting directly:

> For every category of change, the set of environments where it is permitted must not include
> production unless it also includes staging.

Or, in the version that is easier to write and catches most of it: enumerate the change types
your most-privileged environment allows, and assert it is a subset of what your least-privileged
one allows. Ours now is.

We found this in a greenfield policy, thirty lines long, written that week, by someone who was
paying attention because the whole product is about authorisation. If it can hide there, it can
hide in the four-hundred-line policy your organisation has been amending for three years.
