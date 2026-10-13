# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""A thin marketing-campaign integration on Mind Palace.

It is deliberately not the marketing application. It is the part that decides
whether Mind Palace can be *adopted* by one, and it exists to make four claims
testable:

* an organisation's brand memory is reusable by every campaign in that
  organisation, and by nobody else's;
* a campaign's own facts do not bleed into its sibling campaign;
* generated content cannot become authoritative memory without approval;
* one organisation's user cannot reach another organisation's memory, whatever
  they ask for.

What is simulated: content generation. `generator.py` is a deterministic
stand-in for a model, so the example runs with no paid service and no network.
Everything else -- corpus resolution, authorization, the archive -- is the real
thing, reached through the public SDK only.
"""

from marketing_memory.generator import PLATFORMS, generate
from marketing_memory.service import MarketingMemory
from marketing_memory.tenancy import (
    PLATFORM_VARIANTS,
    Campaign,
    Membership,
    Role,
    User,
    Workspace,
    brand_corpus,
    campaign_corpus,
)

__all__ = [
    "PLATFORMS",
    "PLATFORM_VARIANTS",
    "Campaign",
    "MarketingMemory",
    "Membership",
    "Role",
    "User",
    "Workspace",
    "brand_corpus",
    "campaign_corpus",
    "generate",
]
