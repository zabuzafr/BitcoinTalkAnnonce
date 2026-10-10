import urlclean


class TestCleanUrl:
    def test_trailing_emoji_is_cut(self):
        assert urlclean.clean_url("https://github.com/o/r📦") == "https://github.com/o/r"

    def test_box_drawing_is_cut(self):
        assert (
            urlclean.clean_url("https://github.com/o/r━━━━━\U0001f680") == "https://github.com/o/r"
        )

    def test_sentence_punctuation_is_cut(self):
        assert urlclean.clean_url("https://x.io/doc).") == "https://x.io/doc"

    def test_bare_www_gets_scheme(self):
        assert urlclean.clean_url("www.monsite.io") == "https://www.monsite.io"

    def test_port_is_kept(self):
        assert urlclean.clean_url("https://example.com:8080/x") == "https://example.com:8080/x"

    def test_trailing_slash_is_dropped(self):
        assert urlclean.clean_url("https://example.com/") == "https://example.com"

    def test_document_path_is_kept(self):
        url = "https://allocinit.xyz/shields/allocinit.pdf"
        assert urlclean.clean_url(url) == url

    def test_plain_path_segment_is_kept(self):
        url = "https://23skidoo.info/downloads"
        assert urlclean.clean_url(url) == url

    def test_relative_url_is_rejected(self):
        assert urlclean.clean_url("/api/status") == ""

    def test_non_http_scheme_is_rejected(self):
        assert urlclean.clean_url("javascript:alert(1)") == ""

    def test_ftp_is_rejected(self):
        assert urlclean.clean_url("ftp://files.example.com/a") == ""

    def test_single_label_host_is_rejected(self):
        assert urlclean.clean_url("https://intranet/x") == ""

    def test_trailing_dash_run_is_cut(self):
        assert urlclean.clean_url("https://t.me/qbtc" + "-" * 40) == "https://t.me/qbtc"

    def test_bracket_after_path_is_cut(self):
        assert urlclean.clean_url("https://x.io/repo[UPDATE:") == "https://x.io/repo"


class TestGluedLinks:
    def test_glued_announcement_label(self):
        assert urlclean.clean_url("https://zycord.com/Source:") == "https://zycord.com"

    def test_glued_label_inside_segment(self):
        assert (
            urlclean.clean_url("https://veneralabs.org/explorerWhitepaper:")
            == "https://veneralabs.org"
        )

    def test_glued_host_is_repaired(self):
        url = "https://noj.viporlab.netStratum:Code:stratum+tcp://lab.viporlab.net:5090"
        assert urlclean.clean_url(url) == "https://noj.viporlab.net"

    def test_two_urls_glued_on_whitepaper(self):
        url = "https://latebitcoiners.comWhitepaperhttps://latebitcoiners.com/whitepaperBlock"
        assert urlclean.clean_url(url) == "https://latebitcoiners.com/whitepaper"

    def test_split_candidates_separates_two_urls(self):
        found = urlclean.split_candidates(
            "https://github.com/a/bhttps://github.com/c/d"
        )
        assert "https://github.com/a/b" in found
        assert "https://github.com/c/d" in found

    def test_repo_name_ending_with_scheme_is_not_split(self):
        found = urlclean.split_candidates("https://github.com/o/bitfuchttps://bitfuc.com")
        assert found[0] == "https://github.com/o/bitfuc"


class TestCanonicalGithub:
    def test_release_tag_is_reduced_to_root(self):
        raw = "https://github.com/o/r/releases/tag/v1.0.0"
        assert urlclean.canonical_github(raw) == "https://github.com/o/r"

    def test_blob_file_is_reduced_to_root(self):
        raw = "https://github.com/o/r/blob/main/WHITEPAPER.md"
        assert urlclean.canonical_github(raw) == "https://github.com/o/r"

    def test_git_suffix_is_dropped(self):
        raw = "https://github.com/o/aperod-node.gitcd"
        assert urlclean.canonical_github(raw) == "https://github.com/o/aperod-node"

    def test_reserved_path_is_not_a_repo(self):
        assert urlclean.canonical_github("https://github.com/orgs/KpowBIT") == ""

    def test_repo_name_keeps_internal_caps(self):
        raw = "https://github.com/CMUSICAI/CmusicWallet"
        assert urlclean.canonical_github(raw) == raw

    def test_second_url_after_topic_link(self):
        raw = "https://bitcointalk.org/index.php?topic=1.0GitHub:https://github.com/o/r"
        assert urlclean.canonical_github(raw) == "https://github.com/o/r"

    def test_repo_name_ending_with_scheme(self):
        raw = "https://github.com/amperedusk/bitfuchttps://bitfuc.comRelay:"
        assert urlclean.canonical_github(raw) == "https://github.com/amperedusk/bitfuc"

    def test_slash_only_host_is_not_a_repo(self):
        assert urlclean.canonical_github("https://github.com/") == ""

    def test_github_slug(self):
        assert urlclean.github_slug("https://github.com/o/r/releases") == ("o", "r")

    def test_github_slug_without_repo(self):
        assert urlclean.github_slug("https://github.com/o") == ("", "")


class TestBuckets:
    def test_explorer_is_not_project_site(self):
        assert not urlclean.is_project_site("https://solscan.io/account/0x1")

    def test_pool_is_not_project_site(self):
        assert not urlclean.is_project_site("https://pool.example.org")

    def test_project_host_is_site(self):
        assert urlclean.is_project_site("https://mon-projet.io/roadmap")

    def test_whitepaper_keyword_in_path(self):
        assert urlclean.is_whitepaper_url("https://x.io/whitepaper")

    def test_document_extension(self):
        assert urlclean.is_whitepaper_url("https://x.io/paper.pdf")

    def test_homepage_is_not_whitepaper(self):
        assert not urlclean.is_whitepaper_url("https://x.io/")

    def test_host_of_lowercases(self):
        assert urlclean.host_of("https://EXAMPLE.io/x") == "example.io"


class TestLinkVariants:
    def test_glued_word_yields_shorter_candidate(self):
        assert urlclean.link_variants("https://xcoinproject.com/whitepaperWho") == [
            "https://xcoinproject.com/whitepaperWho",
            "https://xcoinproject.com/whitepaper",
        ]

    def test_unambiguous_url_has_one_variant(self):
        assert urlclean.link_variants("https://23skidoo.info/downloads") == [
            "https://23skidoo.info/downloads"
        ]

    def test_document_name_is_never_split(self):
        url = "https://allocinit.xyz/shields/allocinit.pdf"
        assert urlclean.link_variants(url) == [url]

    def test_hyphenated_repo_is_never_split(self):
        url = "https://github.com/o/Zagros-Wallet"
        assert urlclean.link_variants(url) == [url]

    def test_deep_path_keeps_its_prefix(self):
        variants = urlclean.link_variants("https://example.com/one/two/threeHere")
        assert variants == [
            "https://example.com/one/two/threeHere",
            "https://example.com/one/two/three",
            "https://example.com/one/two",
            "https://example.com",
        ]

    def test_origin_is_the_last_resort(self):
        assert urlclean.link_variants("https://x.io/a/bHere")[-1] == "https://x.io"

    def test_invalid_url_has_no_variant(self):
        assert urlclean.link_variants("javascript:alert(1)") == []

    def test_words_are_never_joined_by_a_slash(self):
        assert urlclean.link_variants("https://github.com/o/QueenP2P") == [
            "https://github.com/o/QueenP2P",
            "https://github.com/o/QueenP2",
            "https://github.com/o/Queen",
            "https://github.com/o",
            "https://github.com",
        ]


class TestRejections:
    def test_empty(self):
        assert urlclean.clean_url("") == ""
        assert urlclean.clean_url(None) == ""

    def test_html_markup_does_not_yield_url(self):
        assert urlclean.clean_url('<a href="https://x.io">x</a>') == ""

    def test_url_longer_than_limit_is_truncated_or_rejected(self):
        long_url = "https://example.com/" + "a" * 800
        assert len(urlclean.clean_url(long_url)) <= urlclean.MAX_URL_LENGTH