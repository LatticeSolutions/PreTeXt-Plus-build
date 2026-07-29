
import logging
from io import BytesIO, StringIO
from pathlib import Path
import re
import shutil
import html
from tempfile import TemporaryDirectory
from os import environ
from flask import Flask, render_template, request, send_file, make_response
from flask_cors import CORS
from lxml import etree
import prefig
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

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

# the divisions PreTeXt allows as a child of <pretext>
ROOT_DIVISIONS = ("article", "book", "slideshow")

# what a caller may put in the "target" field, mapped to the format we build
TARGETS = {
    "html": "html",
    "revealjs": "revealjs",
    "slides": "revealjs",
    "slideshow": "revealjs",
}


def root_division(source:str):
    # the build names the output file after the label, so we must know it ahead of
    # time; the tag tells us which target to build when none was requested
    try:
        root = etree.fromstring(source.encode(), parser=_XML_PARSER)
    except etree.XMLSyntaxError:
        return None, None
    for child in root:
        if child.tag in ROOT_DIVISIONS:
            return child.tag, child.get("label") or child.get(XML_ID)
    return None, None


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

def revealjs_target(temp_dir:Path, output_label:str):
    return Project().new_target(
        name="revealjs",
        format="revealjs",
        source=temp_dir/"source.ptx",
        publication=temp_dir/"publication.ptx",
        output_dir=temp_dir/"output",
        # a revealjs build names its output after the source file unless told
        # otherwise; name it after the label so it matches the html targets
        output_filename=f"{output_label}.html",
    )

# the one local stylesheet a reveal.js build links (everything else comes from a CDN)
_REVEAL_CSS_LINK = re.compile(
    r'<link[^>]*href="_static/pretext/css/pretext-reveal\.css"[^>]*>'
)

def inline_reveal_css(output_html:str, output_dir:Path):
    # fold that stylesheet into the page so the returned file stands on its own
    css_path = output_dir/"_static"/"pretext"/"css"/"pretext-reveal.css"
    try:
        css = css_path.read_text()
    except FileNotFoundError:
        return output_html
    return _REVEAL_CSS_LINK.sub(lambda _: f"<style>\n{css}\n</style>", output_html)

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
    # a target sent with the payload wins; otherwise we infer one from the source
    requested_target = (request.form.get('target') or "").strip().lower()
    if requested_target and requested_target not in TARGETS:
        return f"Unknown target \"{html.escape(requested_target)}\"", 400
    target_format = TARGETS.get(requested_target)
    with TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source = request.form.get('source')
        # edit out any xml manifest
        source = re.sub(r'<\?xml.*\?>','', source)
        if re.match(r"<pretext\b", source.lstrip()):
            # use source as-is
            assembled_source = source
            division, label = root_division(source)
            # a <slideshow> is a reveal.js presentation unless told otherwise
            if target_format is None:
                target_format = "revealjs" if division == "slideshow" else "html"
            output_label = request.form.get('output_label') or label or division or "article"
        else:
            # assemble source from template
            if target_format is None:
                target_format = "html"
            assembled_source = render_template(
                "slideshow.ptx" if target_format == "revealjs" else "source.ptx",
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
        zipped = request.form.get('format') == 'zip'
        try:
            if target_format == "revealjs":
                revealjs_target(temp_dir, output_label).build()
            elif zipped:
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
        if zipped:
            if target_format == "revealjs":
                # revealjs targets have no compression option, so zip it ourselves
                zip_path = Path(shutil.make_archive(str(temp_dir/"zipped"), "zip", output_dir))
            else:
                # PreTeXt-CLI names the zip after the source file: source.ptx -> source.zip
                zip_path = output_dir / "source.zip"
            buf = BytesIO(zip_path.read_bytes())
            return send_file(buf, mimetype='application/zip', download_name='output.zip', as_attachment=True)
        output_path = output_dir / f"{output_label}.html"
        try:
            if target_format == "revealjs":
                return inline_reveal_css(output_path.read_text(), output_dir)
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
