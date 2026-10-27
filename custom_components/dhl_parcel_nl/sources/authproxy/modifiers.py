#  SPDX-License-Identifier: Apache-2.0
#  Vendored from authcaptureproxy 1.3.7 (https://github.com/alandtse/auth_capture_proxy),
#  Copyright 2021 Alan Tse. Modified for Parcel Tracking NL: relative imports,
#  no logging of form data, cookies, headers or query strings, lazy imports.
# ruff: noqa
"""
Python Package for auth capture proxy.

Example modifiers.
"""

import logging
import re
from functools import partial
from typing import Callable, Dict, List, Optional, Text

from yarl import URL

from .helper import prepend_url, replace_empty_url, run_func, swap_url

_LOGGER = logging.getLogger(__name__)


def autofill(items: dict, html: Text) -> Text:
    """Autofill input tags in form.

    WARNING: This modifier does not obfuscate debug logs.

    Args:
        items (dict): Dictionary of values to fill. The key the name or id of the form input to fill and the value is the value.
        html (Text): html to convert

    Returns:
        Text: html with values filled in

    """
    from bs4 import BeautifulSoup  # lazy: only autofill/find_urls_bs4

    soup = BeautifulSoup(html, "html.parser")
    if not soup or not html:
        _LOGGER.debug("Soup is empty")
        return ""
    if not items:
        _LOGGER.debug("No items specified; no modifications made")
        return html
    for item, value in items.items():
        for html_tag in soup.find_all(attrs={"name": item}) + soup.find_all(
            attrs={"id": item}
        ):
            if not html_tag.get("value"):
                html_tag["value"] = value
                _LOGGER.debug("Filled %s", str(html_tag))
    return str(soup)


async def replace_matching_urls(old_url: URL, new_url: URL, html: Text) -> Text:
    """Replace urls or parts of a url.

    Args:
        old_url (URL): Old url to find and replace. If there is any additional path, it will be added to the new_url.
        new_url (URL): New url to replace.
        html (Text): Text to replace

    Returns:
        Text: Replaced text
    """
    if not old_url or not new_url:
        _LOGGER.debug("No old_url or new_url specified; not modifying")
        return html

    return await find_urls_bs4(
        partial(
            swap_url,
            ignore_query=True,
            old_url=old_url,
            new_url=new_url,
        ),
        search={},
        exceptions={},
        html=html,
    )


async def replace_empty_action_urls(new_url: URL, html: Text) -> Text:
    """Replace urls of empty action attributes.

    For example, <form id="form" method="post" novalidate action="">

    Args:
        new_url (URL): New url to replace.
        html (Text): Text to replace

    Returns:
        Text: Replaced text
    """
    if not new_url:
        _LOGGER.debug("No new_url specified; not modifying")
        return html

    return await find_urls_bs4(
        partial(
            replace_empty_url,
            new_url=new_url,
        ),
        search={"form": "action"},
        exceptions={},
        html=html,
    )


async def prepend_relative_urls(base_url: URL, html: Text) -> Text:
    """Prepend relative urls with url host.

    This is intended to be used for to place the proxy_url in front of relative urls in src="/

    Args:
        base_url (URL): Base URL to prepend
        html (Text): Text to replace

    Returns:
        Text: Replaced text
    """
    if not base_url:
        _LOGGER.debug("No base_url specified")
        return html
    return await find_urls_bs4(
        partial(prepend_url, base_url), search={}, exceptions={}, html=html
    )


async def find_regex_urls(
    modifier: Optional[Callable] = None,
    patterns: Optional[Dict[Text, Text]] = None,
    html: Text = "",
) -> Text:
    """Find urls for based on regex.

    Seen in Tesla login with MFA enabled.

    Args:
        modifier (Optional[Callable], optional): The function to call. It will take in the html_tag, tag, and attribute and modify the html_tag. Defaults to None.
        patterns ([Dict[Text,Text]): A dictionary of regex patterns to search. Key is name and value is regex string.
        html (Text, optional): Text to replace. Defaults to "".

    Returns:
        Text: Text after applying the modifier to the urls found using the search.
    """
    patterns = patterns or {}
    if not html:
        _LOGGER.debug("html is empty")
        return ""
    if not modifier:
        _LOGGER.debug("No modifier provided; returning unmodified")
        return html
    if not patterns:
        _LOGGER.debug("No patterns provided; returning unmodified")
        return html
    for name, pattern in patterns.items():
        s = re.findall(pattern, html, re.IGNORECASE)
        _LOGGER.debug("Found %s patterns for %s", len(s), name)
        for url_string in s:
            new_url: URL = await run_func(modifier, name="", url=URL(url_string))
            _LOGGER.debug("Replacing %s -> %s", url_string, str(new_url))
            html = re.sub(
                re.escape(url_string), str(new_url), html, count=0, flags=re.IGNORECASE
            )
    return html


async def find_urls_bs4(
    modifier: Optional[Callable] = None,
    search: Optional[Dict[Text, Text]] = None,
    exceptions: Optional[Dict[Text, List[Text]]] = None,
    html: Text = "",
) -> Text:
    """Find urls in html using bs4.

    This function will search using beautifulsoup.find_all() and then apply the modifier function to the found url.

    Args:
        modifier (Optional[Callable], optional): The function to call. It will take in the html_tag, tag, and attribute and modify the html_tag. Defaults to None.
        search (Dict[Text, Text], optional): Search dictionary where keys is a tag and the value is an attribute. Defaults to {}.
        exceptions (Dict[Text, List[Text]], optional): Exceptions dictionary where keys is a tag and the value is a url to not modify. Defaults to {}.
        html (Text, optional): Text to replace. Defaults to "".

    Returns:
        Text: Text after applying the modifier to the urls found using the search.
    """
    search = search or {}
    exceptions = exceptions or {}
    from bs4 import BeautifulSoup  # lazy: only autofill/find_urls_bs4

    soup = BeautifulSoup(html, "html.parser")
    if not html:
        _LOGGER.debug("Soup is empty")
        return ""
    if not modifier:
        _LOGGER.debug("No modifier provided; returning unmodified")
        return html
    for nested_html in soup.find_all("script", type="text/html"):
        if nested_html.contents:
            _LOGGER.debug(
                "Found %s nested html content, searching nested content",
                len(nested_html.contents),
            )
        for content in nested_html.contents:
            content.replace_with(
                await find_urls_bs4(modifier, search, exceptions, str(content))
            )
    search = search or {
        "script": "src",
        "link": "href",
        "form": "action",
        "a": "href",
        "style=True": "style",
        "img": "src",
    }
    exceptions = exceptions or {
        "script": ["void(0)"],
        "form": ["get"],
        "a": ["javascript:void(0)"],
    }
    for tag, attribute in search.items():
        for html_tag in soup.find_all(tag):
            if tag == "style=True":
                # handle inline css background image urls
                # https://developer.mozilla.org/en-US/docs/Web/CSS/background-image
                # this currently only handles background-image as the first attribute
                # TODO: Rewrite regex to handle general case
                pattern = r"(?<=style=[\"']background-image:url\([\"']).*(?=[\"']\))"
                attribute_value = html_tag.get(attribute)
                url: Optional[URL] = URL(str(re.search(pattern, attribute_value)))
                if url is not None and url not in exceptions.get(tag, []):
                    new_value = re.sub(
                        pattern,
                        await run_func(modifier, name="", url=url),
                        attribute_value,
                    )
                    old_value = html_tag[attribute]
                    html_tag[attribute] = new_value
                    if str(old_value) != str(html_tag[attribute]):
                        _LOGGER.debug(
                            "Modified url for style:background-image %s -> %s",
                            url,
                            html_tag[attribute],
                        )
            else:
                url = (
                    URL(html_tag.get(attribute))
                    if html_tag.get(attribute) is not None
                    else None
                )
                if (
                    url is not None
                    and not str(url).startswith("data:")
                    and str(url) not in exceptions.get(tag, [])
                ):
                    old_value = html_tag[attribute]
                    html_tag[attribute] = await run_func(modifier, name="", url=url)
                    if str(old_value) != str(html_tag[attribute]):
                        _LOGGER.debug(
                            "Modified url for %s:%s %s -> %s",
                            tag,
                            attribute,
                            url,
                            html_tag[attribute],
                        )
    return str(soup)
