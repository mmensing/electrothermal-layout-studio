# Rich 15.0.0 generated Unicode and emoji tables

The frozen Rich package contains 21 Unicode-version width tables (4.1 through
17.0) derived from wcwidth, as their own source headers state. The supplied
wcwidth 0.3.0 source has exactly the same wide/zero interval data and VS16 sets
for every one of these tables. This is a data correspondence, not a claim about
the original Rich developer's installed generator version.

`upstream-make-width-tables.py.txt` is the unmodified Rich v15.0.0
`tools/make_width_tables.py` from
<https://github.com/Textualize/rich/blob/v15.0.0/tools/make_width_tables.py>.
Its original MIT notice is retained in `LICENSE.rich.txt`. This generator was
omitted from the upstream Rich source distribution, so it is supplied here as a
preferred generation input. The dependency source part contains the complete,
metadata-normalized wcwidth 0.3.0 source, including the original MIT/Markus Kuhn
permission notice, generation scripts and tables. Its data ultimately derives
from the Unicode Character Database; the Unicode permission notice is supplied
with the application's third-party notices.

To regenerate in a separate development environment, extract the supplied Rich
and wcwidth source archives. Install wcwidth from the local source; provide the
ordinary Black formatting tool if running the unchanged upstream generator.
Place the generator at `rich-source/tools/make_width_tables.py`, run it with
`tools` as the working directory, and compare the resulting Rich
`_unicode_data` tables. Black only formats the output; identical interval data
and VS16 characters, rather than byte-identical formatting, establish the
recorded correspondence. Do not install build tools into an existing frozen
application. No wcwidth Python runtime dependency was added to ETLayout.

The separate `upstream-make-emoji.py.txt` is the unmodified Rich v15.0.0
`tools/make_emoji.py`. Its 3,608 generated name/code entries match the supplied
emoji 0.5.4 `EMOJI_ALIAS_UNICODE` data exactly after the generator's lowercase/
colon-removal transform. Again this proves data correspondence, not a historical
build-environment version. The complete matching emoji source and its original
Taehoon Kim/Kevin Wurster BSD-3-Clause notice are supplied. Run that generator in
a separate environment using the local emoji source; it writes `_emoji_codes.py`
in the current working directory. The generated runtime tables are unmodified.

The `.txt` suffix preserves upstream Python source verbatim as an input asset;
remove that suffix when placing the generator in the extracted upstream tree.
