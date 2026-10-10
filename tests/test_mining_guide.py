"""Tests de l'extraction des réglages de minage (mining_guide)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from mining_guide import (
    find_guide_pages,
    guide_is_usable,
    merge_mining_guides,
    parse_mining_guide,
)


PAGE = """Network   Mainnet
Explorer  https://superknet.com
Pool      pool.xcoinminer.com
(172.96.186.49)
Port      3335
Worker    any name
Password  leave blank
Payout    your xpa1r address"""


def test_reads_labelled_table():
    guide = parse_mining_guide(PAGE)
    assert guide["network"] == "Mainnet"
    assert guide["explorer"] == "https://superknet.com"
    assert guide["pool"] == ["pool.xcoinminer.com", "172.96.186.49"]
    assert guide["port"] == "3335"
    assert guide["worker"] == "any name"
    assert guide["password"] == "leave blank"
    assert guide["payout"] == "your xpa1r address"


def test_stratum_gets_the_port():
    guide = parse_mining_guide(PAGE)
    assert guide["stratum"] == [
        "pool.xcoinminer.com:3335",
        "172.96.186.49:3335",
    ]


def test_reads_announcement_without_line_breaks():
    text = (
        "Pool example from the project:Code:pool.xcoinminer.comport 3335"
        "payout address starts with xpa1rCoinbase matures after 1"
        "Network Mainnet Explorer: https://superknet.com/"
    )
    guide = parse_mining_guide(text)
    assert guide["pool"] == ["pool.xcoinminer.com"]
    assert guide["port"] == "3335"
    assert guide["network"] == "Mainnet"
    assert guide["explorer"] == "https://superknet.com"


def test_ignores_prose_mentioning_pool():
    text = (
        "The pool is written for this chain and lives in the repository. "
        "docs/POOL_OPERATOR.md is the walkthrough."
    )
    guide = parse_mining_guide(text)
    assert guide["pool"] == []
    assert guide["port"] == ""
    assert guide["network"] == ""


def test_empty_text_gives_empty_guide():
    guide = parse_mining_guide("")
    assert guide["pool"] == []
    assert guide["stratum"] == []
    assert guide_is_usable(guide) is False


def test_host_label_inside_url_is_not_a_field():
    guide = parse_mining_guide("Pool: pool.example.com\nPort: 3333")
    assert guide["pool"] == ["pool.example.com"]
    assert guide["port"] == "3333"


def test_stratum_scheme_is_stripped_from_host():
    guide = parse_mining_guide("Stratum: stratum+tcp://eu.pool.example.org:3333")
    assert guide["pool"] == ["eu.pool.example.org:3333"]
    assert guide_is_usable(guide) is True


def test_p2p_port_alone_is_not_a_usable_guide():
    guide = parse_mining_guide("Solo mining only. P2P port 17333. There is no pool.")
    assert guide["port"] == ""
    assert guide_is_usable(guide) is False


def test_pool_discord_link_is_not_a_pool_host():
    guide = parse_mining_guide(
        "Pool: blacksmith.best/pool/plne\nPool Discord: discord.gg/pE22wGn5t\n"
        "Port: 21010"
    )
    assert guide["pool"] == ["blacksmith.best"]
    assert guide["stratum"] == ["blacksmith.best:21010"]


def test_pool_keeps_the_mining_host_and_drops_neighbouring_sites():
    guide = parse_mining_guide(
        "Official pool: pool.example.org See the pool page for the exact port. "
        "Website: example.org/#downloads GitHub releases: github.com/o/r/releases"
    )
    assert guide["pool"] == ["pool.example.org"]


def test_single_host_pool_value_is_kept():
    guide = parse_mining_guide("Pool: solo.example.org\nPort: 4444")
    assert guide["pool"] == ["solo.example.org"]
    assert guide["stratum"] == ["solo.example.org:4444"]


def test_chain_label_is_accepted_as_network():
    guide = parse_mining_guide("Chain: Xid\nPool: p.example.org\nPort: 3333")
    assert guide["network"] == "Xid"


def test_invalid_ipv4_is_dropped():
    guide = parse_mining_guide("Pool: 999.999.999.999")
    assert guide["pool"] == []


def test_port_inside_the_pool_url_is_used():
    text = "Pool: pool.example.org\nCode: t-rex -o stratum+tcp://pool.example.org:3638"
    guide = parse_mining_guide(text)
    assert guide["pool"] == ["pool.example.org:3638"]
    assert guide["port"] == "3638"


def test_glued_label_does_not_swallow_the_host():
    text = "Pool: pool.gaelium.ioDiscord: discord.gg/abcTelegram: t.me/gaelium"
    guide = parse_mining_guide(text)
    assert guide["pool"] == ["pool.gaelium.io"]


def test_pool_needs_a_mining_hint_when_several_hosts_are_glued():
    text = "Pool: pool.example.orgCode: see github.com/org/repo"
    guide = parse_mining_guide(text)
    assert guide["pool"] == ["pool.example.org"]


def test_host_without_hint_is_kept_when_alone():
    guide = parse_mining_guide("Pool: stratum.example.org")
    assert guide["pool"] == ["stratum.example.org"]


def test_merge_keeps_first_value_and_unions_pools():
    first = parse_mining_guide("Pool: a.example.org\nPort: 3333")
    second = parse_mining_guide("Pool: b.example.org\nWorker: bob")
    merged = merge_mining_guides(first, second)
    assert merged["pool"] == ["a.example.org", "b.example.org"]
    assert merged["port"] == "3333"
    assert merged["worker"] == "bob"


def test_guide_pages_stay_on_the_same_host():
    html = (
        '<a href="/mining-guide">Guide</a>'
        '<a href="https://example.org/pool">Pool</a>'
        '<a href="https://blog.example.org/mining">Blog</a>'
        '<a href="https://other.example/mining">Other</a>'
        '<a href="mailto:mine@example.org">Mail</a>'
    )
    pages = find_guide_pages(html, "https://example.org")
    assert pages == [
        "https://example.org/mining-guide",
        "https://example.org/pool",
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Algorithm: RandomX\nPool: p.example.org\nPort: 3333", "RandomX"),
        ("Algo: Scrypt", "Scrypt"),
    ],
)
def test_algorithm_variants(text, expected):
    assert parse_mining_guide(text)["algorithm"] == expected