# The `/pandoc/` endpoint

Converts anything Pandoc can read into PreTeXt, using the custom writer from
[oscarlevin/pandoc-pretext](https://github.com/oscarlevin/pandoc-pretext).

This document records the decisions behind the implementation — particularly the
ones that are not obvious from the code, and the ones still open.

## Working on this from a second machine

```bash
git pull
git submodule update --init      # <- required; see below
pip install -r requirements.txt
```

Three things fail *silently or confusingly* if skipped:

1. **`git pull` does not populate the submodule.** It updates the recorded
   pointer but leaves `pandoc-pretext/` empty. Symptom: every conversion
   returns `503 PreTeXt writer unavailable`. That guard exists precisely so
   this failure names itself instead of surfacing as a cryptic Pandoc error on
   every request. Fresh clones should use `git clone --recurse-submodules`.
2. **`pypandoc_binary` is a 34 MB wheel**, not something the repo carries.
   Without it there is no Pandoc binary at all.
3. **Updating the writer is a two-step commit.** Commit in `pandoc-pretext`
   upstream, then `git submodule update --remote && git add pandoc-pretext &&
   git commit` here. Without the second step the deploy keeps the old writer,
   and nothing warns you.

## How Pandoc gets onto Render

Render's native Python runtime gives no root, so `apt-get install pandoc` is not
available. We use **`pypandoc_binary`**, a wheel that carries a statically
linked Pandoc binary as package data (currently Pandoc 3.9; 34.5 MB wheel,
162 MB unpacked). One line in `requirements.txt`, no dashboard configuration.

Alternatives, if the wheel ever becomes unsuitable:

| Route | Change needed | Trade-off |
|---|---|---|
| `pypandoc_binary` (current) | one line in `requirements.txt` | Pandoc version tracks the wheel, not our choice |
| Download the binary in the build command | Render *Build Command* + a `PATH` env var | Pins any Pandoc version; needs dashboard access |
| Switch the service to Docker | add a `Dockerfile`, change the service runtime | Full control, could add TeX Live for PDF; slower builds, gives up the zero-config runtime |

The one deploy risk is the submodule: Render clones public submodules without
extra configuration, but if that ever stops holding, the fallback is to copy the
three `.lua` files into this repo directly.

## API

```
POST /pandoc/
  token       required
  source      text input           ─┐ one of these two
  file        multipart upload     ─┘
  from        optional; inferred from the upload's extension, else "markdown"
  standalone  "yes" wraps output in <pretext><article>; default is a fragment
```

Responses: `200` PreTeXt as `text/plain`; `400` unsupported or undeterminable
format, or a binary format posted as text; `401` bad token; `422` conversion
failure, with Pandoc's stderr in the body; `504` timeout; `503` writer missing.

`GET /pandoc/` returns a demo form, but only when `DEVELOPMENT=true`, matching
the behaviour of `/`.

## Design decisions

**Pandoc is invoked through `subprocess`, not `pypandoc`.** `pypandoc` is still
in `requirements.txt`, but only as the delivery vehicle for the binary —
`pypandoc.get_pandoc_path()` is the sole API we call. The reason is specific:
`pypandoc` accepts a custom *writer* path fine, but for readers it strips the
extension and validates against Pandoc's built-in format list, so
`pretext-latex-reader.lua` becomes `"pretext"` and is rejected. Since the LaTeX
path depends on that reader, `pypandoc` cannot express the conversion we need.
Going direct also buys a real timeout and native binary-file input, neither of
which `pypandoc` offers.

**LaTeX is routed through `pretext-latex-reader.lua`, never `-f latex`.** The
reader pre-declares `\newtheorem` for every environment the writer knows about,
which is what lets `\begin{theorem}[Lagrange]\label{thm-x}` keep both its title
and its `xml:id`. With plain `-f latex`, Pandoc has no numbering context for an
undeclared environment and drops the bracketed name entirely — irrecoverably,
since nothing remains in the document for the writer to find. The reader also
enables `raw_tex`, so constructs like `tikzpicture` survive as `<latex-image>`
instead of being silently dropped.

**Every conversion runs under `--sandbox`.** This confines Pandoc's reads and
writes to files named on the command line, so hostile input cannot use
`\input{/etc/passwd}` or `<img src="file:///...">` to reach the filesystem. This
was verified to be compatible with the custom writer, including `pretext.lua`'s
sibling `require` of `pretext-environments.lua` — Lua module resolution happens
before the sandbox applies. (Worth re-testing if the writer ever gains new
`require`s or starts reading data files.)

**Output is served as `text/plain`, not `application/xml`.** The input is
attacker-controlled and CORS is open, so we do not want a browser interpreting
converted markup on this origin. Callers paste the result into an editor or feed
it back to the build endpoint; neither needs an XML content type.

**Output defaults to a fragment.** A fragment is what you want when pasting into
an existing document, and it composes with the `/` build endpoint for free: a
fragment starts with `<section>`, which fails the `<pretext` test in `pretext()`,
so it takes the template branch and is wrapped in `<article>` — and a `<section>`
inside an `<article>` is valid PreTeXt. Convert, then build, no glue required.

**Binary formats must arrive as an upload.** `docx`/`odt`/`epub` are zip
containers and cannot survive a form field, so they are listed separately in
`PANDOC_BINARY_FORMATS` and rejected with an explanatory `400` if posted as
text. Uploads are written to a temporary file named by *us* — only the extension
comes from the client — so a hostile filename cannot escape the directory.

**`PANDOC_TIMEOUT_SECONDS` sits below Gunicorn's worker timeout.** Gunicorn
defaults to killing a worker after 30 seconds, so a conversion limit of 30 or
more is unreachable: the worker dies first and the client gets a dropped
connection instead of the `504`. The limit is therefore 25. If Render's start
command ever passes an explicit `--timeout`, this constant should move with it,
staying strictly below.

**The format allowlist is deliberately narrower than Pandoc's.** Every input
format is another parser exposed to untrusted input, so `PANDOC_TEXT_FORMATS`
lists only formats a PreTeXt author plausibly arrives with.

## Verified behaviour

Tested end-to-end against Pandoc 3.9 (the version the wheel ships, not just the
system Pandoc): markdown fragment and standalone; a markdown theorem div
becoming `<theorem xml:id title>`; LaTeX with an *undeclared*
`\begin{theorem}[Lagrange]\label{thm-lag}` recovering both title and `xml:id`; a
`.docx` upload with the format inferred from its extension; and each of the
error paths listed under **API** above. The composition with `/` was checked
directly: a fragment takes the template branch, and the assembled document is
well-formed with `<section>` nested inside `<article>`.

## Open questions

**The `PANDOC_TEXT_FORMATS` allowlist needs an owner's decision.** Currently
omitted: `fb2`, `muse`, `t2t`, `creole`, `twiki`, `vimwiki`, `dokuwiki`, `jira`,
and the bibliography formats `bibtex`/`biblatex`/`csljson`. The bibliography
ones are the interesting case — the writer's known limitation is that citations
become bare `<xref>` elements with no matching `<biblio>` entries, so accepting a
`.bib` upload could be a path to generating those. Whether that belongs in this
endpoint is undecided. Adding a format is a one-line edit to the set, plus an
entry in `PANDOC_EXTENSIONS` if it should auto-detect from a filename.

**There is no request size cap.** `app.config["MAX_CONTENT_LENGTH"]` would
protect all three endpoints, not just this one. Relevant now that `/pandoc/`
accepts file uploads.

**PDF output is not offered and cannot be.** Pandoc shells out to a LaTeX engine
for PDF and none is installed. This is why `pdf` appears in no allowlist — a
clear `400` beats a confusing `422` from a missing `pdflatex`.

## Testing locally

The devcontainer has a system Pandoc, which is older than the wheel's. To force
a specific binary:

```bash
PYPANDOC_PANDOC=/usr/bin/pandoc DEVELOPMENT=true BUILD_TOKEN=token flask run
```

Or exercise the writer directly, without the server:

```bash
pandoc notes.tex -f pandoc-pretext/pretext-latex-reader.lua \
                 -t pandoc-pretext/pretext.lua --sandbox -s
```
