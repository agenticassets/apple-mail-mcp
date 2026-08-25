"""Small AppleScript snippet builders shared by Apple Mail tools."""

from typing import Literal


def indent_block(block: str, indent: str) -> str:
    """Indent every line of *block* after the first, leaving blank lines empty.

    Fragments are spliced into an f-string that already indents the placeholder,
    so the first line must stay bare or it ends up double-indented. AppleScript
    ignores indentation, but the emitted script is read by humans during live
    debugging and is asserted on by script-text tests, so it is kept exact.
    """
    first, *rest = block.split("\n")
    return "\n".join([first] + [f"{indent}{line}" if line else line for line in rest])


def sanitize_field_handler(*, include_attachment_row_delimiter: bool = False, name: str = "sanitize_field") -> str:
    """Return an AppleScript handler that normalizes fields for delimited output."""
    attachment_delimiter_block = ""
    if include_attachment_row_delimiter:
        attachment_delimiter_block = """
        set AppleScript's text item delimiters to ";;"
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to "; "
        set valueText to valueParts as string"""

    return f"""
    on {name}(value)
        try
            set valueText to value as string
        on error
            set valueText to ""
        end try
        set AppleScript's text item delimiters to {{return, linefeed, tab}}
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to " "
        set valueText to valueParts as string
        set AppleScript's text item delimiters to "|||"
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to " | "
        set valueText to valueParts as string{attachment_delimiter_block}
        set AppleScript's text item delimiters to ""
        return valueText
    end {name}
    """


_ISO_DATETIME_HANDLERS = """on pad2(numberValue)
    if numberValue < 10 then
        return "0" & (numberValue as string)
    end if
    return numberValue as string
end pad2

on month_number(monthValue)
    set monthValues to {January, February, March, April, May, June, July, August, September, October, November, December}
    repeat with monthIndex from 1 to 12
        if item monthIndex of monthValues is monthValue then
            return monthIndex
        end if
    end repeat
    return 0
end month_number

on iso_datetime(dateValue)
    set yearValue to year of dateValue as integer
    set monthValue to my month_number(month of dateValue)
    set dayValue to day of dateValue as integer
    set hourValue to hours of dateValue
    set minuteValue to minutes of dateValue
    set secondValue to seconds of dateValue
    return (yearValue as string) & "-" & my pad2(monthValue) & "-" & my pad2(dayValue) & "T" & my pad2(hourValue) & ":" & my pad2(minuteValue) & ":" & my pad2(secondValue)
end iso_datetime"""


def iso_datetime_handlers(*, indent: str = "    ") -> str:
    """Return the ``pad2`` / ``month_number`` / ``iso_datetime`` handler trio.

    ``iso_datetime`` renders a Mail ``date`` as ``YYYY-MM-DDTHH:MM:SS``, which is
    the ``received_at`` field every ``|||``-delimited record row carries. It is
    spelled in AppleScript rather than parsed in Python because Mail's own date
    string is locale-dependent. ``month_number`` exists because ``month of`` is a
    constant (``January``), not an integer, and comparing against the literal list
    is the only ordering AppleScript offers.

    Callers splice the result into a script that already indents its first line,
    so the first line carries no indent and ``indent`` is applied to every line
    after it. Emitting the trio more than once in a single script would redefine
    the handlers, so each script includes it exactly once.
    """
    return indent_block(_ISO_DATETIME_HANDLERS, indent)


def text_offset_handler(*, name: str = "textOffset") -> str:
    """Return an AppleScript handler that finds a substring offset safely."""
    return f"""
    on {name}(haystackText, needleText)
        if needleText is "" then return 0
        set previousDelimiters to AppleScript's text item delimiters
        try
            set AppleScript's text item delimiters to needleText
            set splitItems to text items of haystackText
            if (count of splitItems) is 1 then
                set AppleScript's text item delimiters to previousDelimiters
                return 0
            end if
            set beforeNeedle to item 1 of splitItems
            set AppleScript's text item delimiters to previousDelimiters
            return ((count of characters of beforeNeedle) + 1)
        on error
            set AppleScript's text item delimiters to previousDelimiters
            return 0
        end try
    end {name}
    """


#: Quote-header styles the draft verifier recognizes as the start of a quoted
#: original. Apple Mail writes ``On <date>, <sender> wrote:``; Outlook and
#: Exchange write a ``-----Original Message-----`` separator instead. Both were
#: already accepted as proof that a quote exists, so both must also mark where
#: it begins.
QUOTE_HEADER_MARKERS: tuple[str, ...] = (" wrote:", "-----Original Message-----")


def earliest_quote_offset_handler(
    *,
    name: str = "earliestQuoteOffset",
    text_offset_name: str = "textOffset",
) -> str:
    """Return a handler giving the offset where a draft's quoted original starts.

    Zero means no quote header was found. Requires ``text_offset_handler`` in
    the same script.

    The lowest positive offset across every recognized style, so callers that
    ask "is there a quote?" and callers that ask "where does the body above the
    quote end?" cannot disagree. They did: the body slice keyed on ``" wrote:"``
    alone, so an Outlook-style reply -- whose quote the very next line already
    recognized -- produced offset 0 and left the slice spanning the entire body,
    quoted original included. A signature quoted from an earlier message in the
    thread then read as a signature on the draft itself.
    """
    marker_list = ", ".join(f'"{marker}"' for marker in QUOTE_HEADER_MARKERS)
    return f"""
    on {name}(bodyText)
        set lowestOffset to 0
        repeat with quoteMarker in {{{marker_list}}}
            set markerOffset to my {text_offset_name}(bodyText, contents of quoteMarker)
            if markerOffset > 0 then
                if lowestOffset is 0 or markerOffset < lowestOffset then set lowestOffset to markerOffset
            end if
        end repeat
        return lowestOffset
    end {name}
    """


def body_above_quote_handler(*, name: str = "bodyAboveQuote") -> str:
    """Return a handler slicing off everything from the quoted original onward.

    Takes the offset from ``earliest_quote_offset_handler`` rather than
    recomputing it, so one scan serves both the "is there a quote?" flag and
    this slice.

    Offset 1 means the draft opens with the quote header and has no body of its
    own. Testing ``> 1`` alone and letting that case fall through to the whole
    body reintroduces the false positive this pair exists to remove: text
    quoted from an earlier message in the thread reading as text the draft
    itself contains.
    """
    return f"""
    on {name}(bodyText, quoteOffset)
        if quoteOffset is 0 then return bodyText
        if quoteOffset > 1 then return text 1 thru (quoteOffset - 1) of bodyText
        return ""
    end {name}
    """


def thread_headers_block(
    *,
    message_var: str,
    in_reply_to_var: str,
    references_var: str,
    sanitize_fn: str | None = "sanitize_field",
    include_on_error: bool = False,
) -> str:
    """Return an AppleScript block that reads In-Reply-To and References headers."""

    def _value_expr(offset: int) -> str:
        raw = f"text {offset} thru -1 of headerLineText"
        if sanitize_fn is None:
            return raw
        return f"my {sanitize_fn}({raw})"

    on_error_block = ""
    if include_on_error:
        on_error_block = f'''
                on error
                    set {in_reply_to_var} to ""
                    set {references_var} to ""'''

    return f"""
                set {in_reply_to_var} to ""
                set {references_var} to ""
                try
                    set msgHeaders to all headers of {message_var}
                    set AppleScript's text item delimiters to {{return, linefeed}}
                    set headerLines to text items of msgHeaders
                    set AppleScript's text item delimiters to ""
                    repeat with headerLine in headerLines
                        set headerLineText to headerLine as string
                        ignoring case
                            if headerLineText starts with "In-Reply-To:" and length of headerLineText > 12 then
                                set {in_reply_to_var} to {_value_expr(13)}
                            else if headerLineText starts with "References:" and length of headerLineText > 11 then
                                set {references_var} to {_value_expr(12)}
                            end if
                        end ignoring
                    end repeat{on_error_block}
                end try
    """


def recipient_addresses_block(
    *,
    message_var: str,
    recipient_kind: Literal["to", "cc", "bcc"],
    output_var: str,
    sanitize_fn: str | None = "sanitize_field",
    include_on_error: bool = False,
) -> str:
    """Return an AppleScript block that collects one recipient kind from one message."""
    list_var = f"{recipient_kind}Addrs"
    value_expr = f"{list_var} as string"
    if sanitize_fn is not None:
        value_expr = f"my {sanitize_fn}({value_expr})"
    on_error_block = ""
    if include_on_error:
        on_error_block = f'''
                on error
                    set {output_var} to ""'''

    return f'''
                set {output_var} to ""
                try
                    set {list_var} to {{}}
                    repeat with aRecip in ({recipient_kind} recipients of {message_var})
                        try
                            set end of {list_var} to address of aRecip
                        end try
                    end repeat
                    set AppleScript's text item delimiters to ", "
                    set {output_var} to {value_expr}
                    set AppleScript's text item delimiters to ""{on_error_block}
                end try
    '''
