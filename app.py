
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

def standalone_target(temp_dir:Path):
    return Project().new_target(
        name="standalone",
        format="html",
        standalone="yes",
        source=temp_dir/"source.ptx",
        publication=temp_dir/"publication.ptx",
        output_dir=temp_dir/"output",
    )


@app.route("/", methods=["GET", "POST"])
def api():
    if request.method == "GET":
        if environ.get("DEVELOPMENT") == "true":
            return render_template("api.html", token=TOKEN)
        return "PreTeXt.Plus Build API"
    if request.form.get('token') != TOKEN:
        return "Invalid token", 401
    with TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        # write source to file temp_dir/source.ptx
        (temp_dir/"source.ptx").write_text(render_template(
            "source.ptx",
            source=request.form.get('source'),
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
{log_stream.getvalue()}
</pre>
            """
            log_stream.seek(0)
            log_stream.truncate(0)
            return response, 500
        # return the generated HTML file
        return (temp_dir / "output" / "article.html").read_text()
