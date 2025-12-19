
import logging
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from os import environ
from flask import Flask, render_template, request
from pretext.project import Project
from pretext.logger import get_log_error_flush_handler

app = Flask(__name__)

log = logging.getLogger("ptxlogger")
log_stream = StringIO()
log_handler = logging.StreamHandler(log_stream)
log.addHandler(log_handler)

# get token from environment
TOKEN = environ.get("BUILD_TOKEN")

def standalone_target(source_path:Path,temp_dir:Path):
    return Project().new_target(
        name="standalone",
        format="html",
        source=source_path,
        standalone="yes",
        output_dir=temp_dir,
    )


@app.route("/", methods=["GET", "POST"])
def api():
    if request.method == "GET":
        if environ.get("DEVELOPMENT") == "true":
            return render_template("api.html", token=TOKEN)
        return "PreTeXt.Plus Build API"
    with TemporaryDirectory() as temp_dir_name:
        if request.form.get('token') != TOKEN:
            return "Invalid token", 401
        temp_dir = Path(temp_dir_name)
        source_path = temp_dir/"source.ptx"
        # write ptx_source to file temp_dir/source.ptx
        source_path.write_text(render_template(
            "source.ptx",
            source=request.form.get('source'),
            title=request.form.get('title'),
        ))
        # build standalone target
        try:
            standalone_target(source_path, temp_dir).build()
        except Exception as e:
            response = f"""
<h2>{e}</h2>
<h3>Error logs:</h3>
<pre>
{log_stream.getvalue()}
</pre>
            """
            log_stream.seek(0)
            log_stream.truncate(0)
            return response, 500
        # return the generated HTML file
        return (temp_dir / "article.html").read_text()
