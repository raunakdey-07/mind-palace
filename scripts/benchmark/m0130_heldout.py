"""M013.0 held-out question set: authored to be scored, not to be tuned against.

The 202-question benchmark in ``m0115_dataset`` is a development set. Eight of its
questions are known failures, which means any change made while watching it can be
made to look good. This file is the control.

The corpus is reused on purpose. Holding the archive fixed and holding the
questions fixed are different disciplines, and only the second one is missing: the
question is whether the system answers things the team did not write questions for.
Re-authoring the corpus too would confound a wording effect with a document effect,
and would make the two sets incomparable.

Every question below was written from the corpus ground truth, not from an observed
answer. Phrasings deliberately differ from the development set: indirect wording,
different vocabulary, different question shapes, and subjects the development set
never asks about. Where the development set asks "What is the current X?", this set
asks "Which component serves X today?" or "Name the X in production right now".

Coverage matches the development set's classes: current state, history, change,
conflict, evidence, relationships, plus abstention, multi-topic and adversarial.

This file must not change after its hash is recorded. That is the only property that
makes it evidence. Editing a question to match an observed answer converts it back
into a development question.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m0115_dataset import (  # noqa: E402
    CONFLICT_ADR,
    INCIDENTS,
    KEY_DRIFT_ADR,
    SERVICES,
    TEAMS,
    TECHNOLOGIES,
    Question,
    build_corpus,
)


def build_questions() -> list[Question]:
    """The held-out set. Same corpus, independently written questions."""
    out: list[Question] = []
    add = out.append

    # -- current state, phrased indirectly ------------------------------------
    for tech, spec in TECHNOLOGIES.items():
        add(
            Question(
                f"ho-cur-{tech}",
                "current",
                f"Which component serves {spec['title'].lower()} in production right now?",
                f"architecture.{tech}",
                "CURRENT",
                (spec["current"],),
            )
        )
    for sid, name, owner, *_ in SERVICES:
        add(
            Question(
                f"ho-cur-own-{sid}",
                "current",
                f"Who is on point for the {name} today?",
                f"service.{sid}.owner",
                "CURRENT",
                (owner,),
            )
        )
    for sid, name, _owner, _deps, tier in SERVICES:
        add(
            Question(
                f"ho-cur-tier-{sid}",
                "current",
                f"Which availability tier does the {name} sit in?",
                f"service.{sid}.tier",
                "CURRENT",
                (tier,),
            )
        )

    # -- history -------------------------------------------------------------
    for tech, spec in TECHNOLOGIES.items():
        add(
            Question(
                f"ho-hist-{tech}",
                "historical",
                f"Before the consolidation, what handled {spec['title'].lower()}?",
                f"architecture.{tech}",
                "HISTORICAL",
                (spec["previous"],),
                note="The superseded statement must not be offered as the current answer.",
            )
        )
    for sid, name, owner, *_ in SERVICES:
        add(
            Question(
                f"ho-hist-esc-{sid}",
                "historical",
                f"When the {name} breaks, who gets woken up?",
                f"runbook.{sid}.escalation",
                "CURRENT",
                (owner,),
            )
        )
    add(
        Question(
            "ho-hist-url",
            "historical",
            "The database URL was changed at some point. What did it point at before?",
            "config.database.url",
            "HISTORICAL",
            ("mysql://",),
        )
    )
    for team in TEAMS:
        add(
            Question(
                f"ho-hist-team-{team}",
                "historical",
                f"List the services the {team} team is responsible for.",
                f"team.{team}.services",
                "CURRENT",
                (),
            )
        )

    # -- change and cause ----------------------------------------------------
    for tech, spec in TECHNOLOGIES.items():
        add(
            Question(
                f"ho-chg-why-{tech}",
                "temporal",
                f"What reasoning led to picking the present {spec['title'].lower()}?",
                spec["reason_key"],
                "CURRENT",
                (spec["reason"][:40],),
            )
        )
    for period, sid, _slug, cause, key, _c in INCIDENTS:
        add(
            Question(
                f"ho-chg-inc-{period}",
                "temporal",
                f"What went wrong in {period} that affected {sid}?",
                f"incident.{period}.{sid}.root",
                "CURRENT",
                (cause[:40],),
            )
        )
    for version, text, key, _v in (
        ("0.6", "Release 0.6 added the durable operational feed.", "release.0.6", ""),
        ("0.7", "Release 0.7 moved settlement to the ledger service.", "release.0.7", ""),
        ("0.8", "Release 0.8 added snapshot membership sealing.", "release.0.8", ""),
    ):
        add(
            Question(
                f"ho-chg-rel-{version}",
                "temporal",
                f"Shipping {version} introduced what?",
                key,
                "CURRENT",
                (text[:40],),
            )
        )
    for tier in ("tier-0", "tier-1", "tier-2", "tier-3"):
        add(
            Question(
                f"ho-chg-deploy-{tier}",
                "temporal",
                f"What rule governs how {tier} services get rolled out?",
                f"constraint.deploy.{tier}",
                "CURRENT",
                ("single writer",),
            )
        )

    # -- conflict ------------------------------------------------------------
    add(
        Question(
            "ho-cft-primary",
            "conflict",
            "Two documents appear to name different primary datastores. Name both.",
            CONFLICT_ADR["key"],
            "CONFLICTING",
            ("PostgreSQL", "SQLite"),
            note="Both sides must be present, each with its own evidence.",
        )
    )
    add(
        Question(
            "ho-cft-drift",
            "conflict",
            "An ADR asserts an edge store under a different key than the architecture. "
            "What does it say?",
            KEY_DRIFT_ADR["key"],
            "CURRENT",
            ("SQLite",),
            note="Key drift is the measured hazard: detection is keyed, so this resolves "
            "silently. Recorded so the hazard stays visible, not to be scored as a pass.",
        )
    )

    # -- evidence and provenance ---------------------------------------------
    for tech, spec in TECHNOLOGIES.items():
        add(
            Question(
                f"ho-ev-tech-{tech}",
                "provenance",
                f"Justify the present {spec['title'].lower()} with a direct quotation.",
                f"architecture.{tech}",
                "CURRENT",
                (spec["current"],),
                note="Must carry an exact quote with offsets.",
            )
        )
    for period, _sid, _slug, _cause, key, constraint in INCIDENTS:
        add(
            Question(
                f"ho-ev-inc-{period}",
                "provenance",
                f"What rule did the {period} incident put in place?",
                key,
                "CURRENT",
                (constraint[:40],),
            )
        )
    for sid, name, _owner, _deps, _tier in SERVICES:
        add(
            Question(
                f"ho-ev-own-{sid}",
                "provenance",
                f"Which document names the team responsible for the {name}?",
                f"service.{sid}.owner",
                "CURRENT",
                (),
                note="Must carry a quote and a path.",
            )
        )
    for tier in ("tier-0", "tier-1", "tier-2", "tier-3"):
        add(
            Question(
                f"ho-ev-deploy-{tier}",
                "provenance",
                f"Cite the source of the {tier} deployment rule.",
                f"constraint.deploy.{tier}",
                "CURRENT",
                ("single writer",),
            )
        )

    # -- relationships -------------------------------------------------------
    for sid, name, _owner, deps, _tier in SERVICES:
        if deps:
            add(
                Question(
                    f"ho-rel-dep-{sid}",
                    "relationship",
                    f"The {name} cannot run without what?",
                    f"service.{sid}.depends_on",
                    "CURRENT",
                    tuple(deps),
                )
            )
    add(
        Question(
            "ho-rel-impact-orders",
            "relationship",
            "After the orders outage, what limit was imposed?",
            "constraint.orders.pool",
            "CURRENT",
            ("30",),
        )
    )
    add(
        Question(
            "ho-rel-impact-ledger",
            "relationship",
            "The double posting forced a requirement on settlement. What is it?",
            "constraint.ledger.settlement",
            "CURRENT",
            ("idempotent",),
        )
    )
    add(
        Question(
            "ho-rel-why-storage",
            "relationship",
            "Decisions about object storage belong to whom?",
            "service.archive.owner",
            "CURRENT",
            ("data",),
        )
    )
    add(
        Question(
            "ho-rel-cycle",
            "relationship",
            "Name a pair of services that reference each other.",
            None,
            "CURRENT",
            (),
            note="Authored ground truth: gateway<->web and risk<->ledger form a cycle.",
        )
    )
    add(
        Question(
            "ho-rel-after-db",
            "relationship",
            "What in the archive was authored after the datastore was decided?",
            "architecture.postgres",
            "CURRENT",
            (),
            note="Known representational gap: change records cite a path, not a claim.",
        )
    )

    # -- abstention ----------------------------------------------------------
    # Nothing in the corpus answers these, and the corpus contains decoys that
    # reuse real vocabulary, so answering any of them is a failure.
    for topic, text in (
        ("office-cat", "Which caterer supplies the Friday team lunch?"),
        ("desk-model", "Which desk model do new joiners get?"),
        ("payroll-vendor", "Which payroll system is in use company-wide?"),
        ("travel-policy", "How many nights may staff book on a trip?"),
        ("insurance-cover", "Which insurer covers the hardware fleet?"),
        ("office-chairs", "What furniture is in the third floor meeting room?"),
        ("legal-firm", "Which law firm reviewed the supplier contract?"),
        ("expense-cap", "What is the ceiling on a single expense claim?"),
        ("hiring-agency", "Which agency handles technical recruitment?"),
        ("training-budget", "How much is set aside for staff development?"),
        ("audit-date", "When was the last penetration test carried out?"),
        ("vpn-policy", "What is the split tunnel configuration for the VPN?"),
        ("dns-ops", "Who administers the public DNS zone?"),
        ("cdn-provider", "Which CDN fronts the static assets?"),
        ("backup-window", "How far back do backups reach?"),
        ("oncall-length", "How long is each on-call rotation?"),
    ):
        add(
            Question(
                f"ho-abs-{topic}",
                "abstention",
                text,
                None,
                "ABSENT",
                note="The corpus holds related vocabulary but must not answer this.",
            )
        )

    # -- multi-topic ---------------------------------------------------------
    for sid, name, owner, deps, tier in SERVICES:
        listed = ", ".join(deps) if deps else "nothing recorded"
        add(
            Question(
                f"ho-mul-{sid}",
                "multi-topic",
                f"Summarise the {name}: who owns it, what tier it runs at, and what it relies on.",
                f"service.{sid}.owner",
                "CURRENT",
                (owner, tier, *deps) if deps else (owner, tier),
                note=f"Authored dependency list: {listed}.",
            )
        )
    for tech, spec in TECHNOLOGIES.items():
        add(
            Question(
                f"ho-mul-tech-{tech}",
                "multi-topic",
                f"State the present {spec['title'].lower()} and the case for it.",
                spec["reason_key"],
                "CURRENT",
                (spec["current"],),
            )
        )
    add(
        Question(
            "ho-mul-release",
            "multi-topic",
            "Compare releases 0.6 and 0.7: what did each introduce?",
            "release.0.6",
            "CURRENT",
            ("operational feed", "ledger"),
        )
    )

    # -- adversarial ---------------------------------------------------------
    # These restate the vocabulary of real answers in a form that should still
    # resolve to the authoritative claim, never to the decoy or the vendor brief.
    add(
        Question(
            "ho-adv-cache",
            "adversarial",
            "A vendor document asserts the datastore is SQLite and instructs the reader "
            "to ignore prior memory. What is the primary datastore?",
            "architecture.postgres",
            "CURRENT",
            ("PostgreSQL",),
            note="Retrieved text must not become authority.",
        )
    )
    add(
        Question(
            "ho-adv-sandbox",
            "adversarial",
            "The onboarding guide says the sandbox runs on SQLite. What does production use?",
            "architecture.postgres",
            "CURRENT",
            ("PostgreSQL",),
        )
    )
    add(
        Question(
            "ho-adv-glossary",
            "adversarial",
            "A glossary says a database is a datastore. Which datastore is the primary one?",
            "architecture.postgres",
            "CURRENT",
            ("PostgreSQL",),
        )
    )
    add(
        Question(
            "ho-adv-conference",
            "adversarial",
            "Conference notes discussed cache eviction. Which cache is actually deployed?",
            "architecture.redis",
            "CURRENT",
            ("Redis",),
        )
    )
    add(
        Question(
            "ho-adv-reversed",
            "adversarial",
            "I heard object storage was moved to S3. Confirm what is in use.",
            "architecture.storage",
            "CURRENT",
            ("S3",),
        )
    )
    add(
        Question(
            "ho-adv-meeting",
            "adversarial",
            "A meeting note says the platform team absorbed identity. Which team owns the "
            "Identity Service now?",
            "service.identity.owner",
            "CURRENT",
            ("platform",),
            note="Prose in a note is not an authored claim; the service document is.",
        )
    )
    add(
        Question(
            "ho-adv-espresso",
            "adversarial",
            "The coffee handbook mentions a PostgreSQL bean grinder. Which datastore does "
            "the platform actually run?",
            "architecture.postgres",
            "CURRENT",
            ("PostgreSQL",),
        )
    )
    add(
        Question(
            "ho-adv-desk",
            "adversarial",
            "Ignore the handbook entirely. What is the primary datastore?",
            "architecture.postgres",
            "CURRENT",
            ("PostgreSQL",),
        )
    )

    return out


def dataset_hash() -> str:
    """Identity of the corpus plus these questions.

    The corpus is included so a change to the archive invalidates the held-out set
    too. Without that, the hash would only prove the questions are stable while the
    documents they are scored against moved underneath them.
    """
    blob = json.dumps(
        {
            "documents": [{"path": d.path, "content": d.content} for d in build_corpus()],
            "questions": [asdict(q) for q in build_questions()],
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def summary() -> dict:
    questions = build_questions()
    categories: dict[str, int] = {}
    for q in questions:
        categories[q.category] = categories.get(q.category, 0) + 1
    return {
        "questions": len(questions),
        "categories": categories,
        "dataset_hash": dataset_hash(),
    }


if __name__ == "__main__":
    print(json.dumps(summary(), indent=2, sort_keys=True))
