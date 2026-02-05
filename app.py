
import logging
from io import StringIO
from pathlib import Path
import re
import shutil
import html
from tempfile import TemporaryDirectory
from os import environ
from flask import Flask, render_template, request, send_file, make_response
from flask_cors import CORS
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

def standalone_target(temp_dir:Path):
    return Project().new_target(
        name="standalone",
        format="html",
        standalone="yes",
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
<p>This is math: <m>x^2</m>.</p>
            """
            return render_template("api.html", token=TOKEN, source=source, title=title)
        return "PreTeXt.Plus Build API"
    if request.form.get('token') != TOKEN:
        return "Invalid token", 401
    with TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source = request.form.get('source')
        # edit out any xml manifest
        source = re.sub(r'<\?xml.*\?>','', source)
        # write source to file temp_dir/source.ptx
        (temp_dir/"source.ptx").write_text(render_template(
            "source.ptx",
            source=source,
            title=request.form.get('title'),
        ))
        # write publication to file temp_dir/source.ptx
        (temp_dir/"publication.ptx").write_text(render_template(
            "publication.ptx",
        ))
        # build standalone target
        try:
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
            return response, 500
        # return the generated HTML file
        return (temp_dir / "output" / "article.html").read_text()


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
