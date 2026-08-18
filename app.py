
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
