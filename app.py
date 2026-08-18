
import logging
from io import BytesIO, StringIO
from pathlib import Path
import re
import shutil
import subprocess
import html
from tempfile import TemporaryDirectory
from os import environ
from flask import Flask, render_template, request, send_file, make_response
from flask_cors import CORS
from lxml import etree
import prefig
import pypandoc
from pretext.project import Project
from pretext.logger import get_log_error_flush_handler

app = Flask(__name__)
CORS(app)

log = logging.getLogger("ptxlogger")
log_stream = StringIO()
log_handler = logging.StreamHandler(log_stream)
log.addHandler(log_handler)

# get token from environment
TOKEN = environ.get("BUILD_TOKEN")

_XML_PARSER = etree.XMLParser(
    resolve_entities=False, load_dtd=False, no_network=True, dtd_validation=False, huge_tree=False
)

def root_label(source:str):
    # the build names the output file after this label, so we must know it ahead of time
    try:
        root = etree.fromstring(source.encode(), parser=_XML_PARSER)
    except etree.XMLSyntaxError:
        return None
    for child in root:
        if child.tag in ("article", "book", "slideshow"):
            if "label" in child.attrib:
                return child.get("label")
            elif "xml:id" in child.attrib:
                return child.get("xml:id")
    return None


def standalone_target(temp_dir:Path):
    return Project().new_target(
        name="standalone",
        format="html",
        standalone="yes",
        source=temp_dir/"source.ptx",
        publication=temp_dir/"publication.ptx",
        output_dir=temp_dir/"output",
    )

def zipped_target(temp_dir:Path):
    return Project().new_target(
        name="zipped",
        format="html",
        compression="zip",
        source=temp_dir/"source.ptx",
        publication=temp_dir/"publication.ptx",
        output_dir=temp_dir/"output",
    )

@app.route("/external/icon.svg")
def icon_svg():
    return send_file("icon.svg")


@app.route("/", methods=["GET", "POST"])
def pretext():
    if request.method == "GET":
        if environ.get("DEVELOPMENT") == "true":
            title = r"Hello world! Goodbye <m>\LaTeX</m>!"
            source = """
<pretext>
<article xml:id="article">
<title>My Article</title>
<introduction><p>Hello world.</p></introduction>
<section><title>Section First</title><p>Heya.</p></section>
<section><title>Second Section</title><p>Goodbye.</p></section>
</article>
</pretext>
            """
            return render_template("api.html", token=TOKEN, source=source, title=title)
        return "PreTeXt.Plus Build API"

    # Otherwise, request.method == "POST"
    if request.form.get('token') != TOKEN:
        return "Invalid token", 401
    with TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source = request.form.get('source')
        # edit out any xml manifest
        source = re.sub(r'<\?xml.*\?>','', source)
        if re.match(r"<pretext\b", source.lstrip()):
            # use source as-is
            assembled_source = source
            output_label = request.form.get('output_label') or root_label(source) or "article"
        else:
            # assemble source from template
            assembled_source = render_template(
                "source.ptx",
                source=source,
                title=request.form.get('title'),
            )
            output_label = "output"
        # write source to file temp_dir/source.ptx
        (temp_dir/"source.ptx").write_text(assembled_source)
        # write publication to file temp_dir/publication.ptx
        if request.form.get("format") == "zip":
            chunking = "1"
        else:
            chunking = "0"
        (temp_dir/"publication.ptx").write_text(render_template(
            "publication.ptx",chunking=chunking
        ))
        # build appropriate target
        try:
            if request.form.get('format') == 'zip':
                zipped_target(temp_dir).build()
            else:
                standalone_target(temp_dir).build()
        except Exception as e:
            response = f"""
<h2>{e}</h2>
<h3>Error logs:</h3>
<pre>
{html.escape(log_stream.getvalue())}
</pre>
            """
            log_stream.seek(0)
            log_stream.truncate(0)
            return response, 422  # 422 Unprocessable Content https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/422
        # return ZIP of all build files or just the HTML file
        output_dir = temp_dir / "output"
        if request.form.get('format') == 'zip':
            # PreTeXt-CLI names the zip after the source file: source.ptx -> source.zip
            zip_path = output_dir / "source.zip"
            buf = BytesIO(zip_path.read_bytes())
            return send_file(buf, mimetype='application/zip', download_name='output.zip', as_attachment=True)
        output_path = output_dir / f"{output_label}.html"
        try:
            return output_path.read_text()
        except FileNotFoundError:
            produced = sorted(f.name for f in (temp_dir / "output").glob("*.html"))
            response = f"""
<h2>Expected output file "{html.escape(output_path.name)}" was not found.</h2>
<p>The build succeeded, but no file matched the output_label "{html.escape(output_label)}".
This usually means the source's &lt;article&gt;, &lt;book&gt;, or &lt;slideshow&gt;
element doesn't carry a matching label attribute.</p>
<h3>Files produced by the build:</h3>
<pre>
{html.escape(", ".join(produced) or "(none)")}
</pre>
            """
            return response, 500


@app.route("/prefigure/", methods=["GET", "POST"])
def prefigure():
    if request.method == "GET":
        source = """
<diagram dimensions="(300,300)" margins="5">
  <definition> f(x) = exp(x/3)*cos(x) </definition>
  <definition> a = 1 </definition>
  <coordinates bbox="(-4,-4,4,4)">
    <grid-axes xlabel="x" ylabel="y"/>    
    <graph function="f"/>
    <tangent-line function="f" point="a"/>
    <point p="(a,f(a))">
      <m>(a,f(a))</m>
    </point>
  </coordinates>
</diagram>
        """
        return render_template("api.html", token=TOKEN, source=source, title=None)
        # if environ.get("DEVELOPMENT") == "true":
        #     return render_template("api.html", token=TOKEN)
        # return "PreTeXt.Plus Prefigure Build API"
    if request.form.get('token') != TOKEN:
        return "Invalid token", 401
    source = request.form.get('source')
    svg = prefig.engine.build_from_string('svg', source, environment="pretext")
    response =  make_response(svg)
    response.headers['Content-type'] = 'image/svg+xml'
    return response

# --- Pandoc to PreTeXt -------------------------------------------------------

# The custom writer from https://github.com/oscarlevin/pandoc-pretext, vendored
# as a git submodule. pretext.lua and pretext-latex-reader.lua both require
# pretext-environments.lua from beside themselves, so the whole directory has to
# travel together.
PANDOC_PRETEXT_DIR = Path(__file__).parent / "pandoc-pretext"
PRETEXT_WRITER = PANDOC_PRETEXT_DIR / "pretext.lua"
PRETEXT_LATEX_READER = PANDOC_PRETEXT_DIR / "pretext-latex-reader.lua"

# Input formats we accept. Pandoc reads more than this, but these are the ones
# a PreTeXt author plausibly arrives with, and every additional entry is another
# parser exposed to untrusted input.
PANDOC_TEXT_FORMATS = {
    "commonmark_x", "docbook", "gfm", "html", "ipynb", "jats", "latex",
    "markdown", "markdown_mmd", "markdown_strict", "mediawiki", "org", "rst",
    "textile", "typst",
}
# Formats that are zip containers or otherwise not text: these must arrive as a
# file upload rather than in the `source` form field.
PANDOC_BINARY_FORMATS = {"docx", "odt", "epub"}

# Maps an uploaded file's extension to a pandoc input format, so callers can
# just post a file without also naming its format.
PANDOC_EXTENSIONS = {
    ".docx": "docx", ".odt": "odt", ".epub": "epub", ".tex": "latex",
    ".ltx": "latex", ".md": "markdown", ".markdown": "markdown",
    ".html": "html", ".htm": "html", ".rst": "rst", ".org": "org",
    ".ipynb": "ipynb", ".typ": "typst", ".xml": "docbook", ".textile": "textile",
}

PANDOC_TIMEOUT_SECONDS = 60


def pandoc_pretext_args(from_format: str, standalone: bool) -> list[str]:
    """Build the pandoc arguments for a conversion into PreTeXt.

    LaTeX is routed through the companion reader rather than `-f latex`: it
    pre-declares \\newtheorem for every environment the writer knows, which is
    what lets \\begin{theorem}[Lagrange] keep its title, and it enables raw_tex
    so that e.g. tikzpicture survives as <latex-image> instead of being dropped.
    """
    if from_format == "latex":
        reader = str(PRETEXT_LATEX_READER)
    else:
        reader = from_format
    # --sandbox confines pandoc's reads and writes to the files named here, so
    # a hostile \input{} or <img src="file:///..."> can't reach the filesystem.
    # It does not interfere with loading the custom writer or its sibling
    # require, both of which are resolved before the sandbox applies.
    args = ["--from", reader, "--to", str(PRETEXT_WRITER), "--sandbox"]
    if standalone:
        args.append("--standalone")
    return args


def run_pandoc(args: list[str], stdin_bytes: bytes | None = None) -> str:
    """Run pandoc, raising RuntimeError with its stderr if the conversion fails."""
    result = subprocess.run(
        [pypandoc.get_pandoc_path(), *args],
        input=stdin_bytes,
        capture_output=True,
        timeout=PANDOC_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("utf-8", "replace")


@app.route("/pandoc/", methods=["GET", "POST"])
def pandoc():
    if request.method == "GET":
        if environ.get("DEVELOPMENT") == "true":
            source = "# Hello world\n\nThis is math: $x^2$.\n"
            return render_template("pandoc.html", token=TOKEN, source=source)
        return "PreTeXt.Plus Pandoc API"

    # Otherwise, request.method == "POST"
    if request.form.get('token') != TOKEN:
        return "Invalid token", 401

    # The writer lives in a git submodule; if the deploy didn't clone it, say so
    # plainly rather than letting pandoc fail with a cryptic message per request.
    if not PRETEXT_WRITER.is_file():
        log.error("pretext.lua not found at %s -- is the pandoc-pretext "
                  "submodule initialized?", PRETEXT_WRITER)
        return "PreTeXt writer unavailable on this server", 503

    upload = request.files.get('file')
    from_format = request.form.get('from')
    # An uploaded file names its own format via its extension unless the caller
    # says otherwise; a pasted source defaults to markdown.
    if not from_format:
        if upload and upload.filename:
            from_format = PANDOC_EXTENSIONS.get(
                Path(upload.filename).suffix.lower(), ""
            )
            if not from_format:
                return (f"Could not determine a format for "
                        f"{html.escape(upload.filename)}; pass a 'from' field."), 400
        else:
            from_format = "markdown"
    if from_format not in PANDOC_TEXT_FORMATS | PANDOC_BINARY_FORMATS:
        return f"Unsupported input format: {html.escape(from_format)}", 400

    source = request.form.get('source')
    if not upload and not source:
        return "No source provided: post a 'source' field or a 'file' upload", 400
    if not upload and from_format in PANDOC_BINARY_FORMATS:
        return (f"{from_format} is a binary format; "
                f"post it as a 'file' upload rather than a 'source' field"), 400

    # Default to a fragment, which is what you want when pasting the result into
    # an existing PreTeXt document; --standalone wraps it in <pretext><article>.
    standalone = request.form.get('standalone') in ("yes", "true", "1")
    args = pandoc_pretext_args(from_format, standalone)

    with TemporaryDirectory() as temp_dir_name:
        try:
            if upload:
                # Binary readers need a real file, not stdin. The name is ours,
                # never the client's, so a hostile filename can't escape.
                temp_path = Path(temp_dir_name) / f"input{Path(upload.filename or '').suffix.lower()}"
                upload.save(temp_path)
                converted = run_pandoc([*args, str(temp_path)])
            else:
                converted = run_pandoc(args, stdin_bytes=source.encode("utf-8"))
        except subprocess.TimeoutExpired:
            return f"Conversion timed out after {PANDOC_TIMEOUT_SECONDS}s", 504
        except RuntimeError as e:
            return f"""
<h2>Pandoc conversion failed</h2>
<pre>
{html.escape(str(e))}
</pre>
            """, 422

    # text/plain rather than application/xml: the caller pastes this into an
    # editor or feeds it back to the build endpoint, and we don't want a browser
    # interpreting attacker-supplied markup on this origin.
    response = make_response(converted)
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    return response
