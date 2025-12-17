from pathlib import Path
from tempfile import TemporaryDirectory
from os import environ
from flask import Flask, render_template, request
from pretext.project import Project

app = Flask(__name__)

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
        # create standalone target
        target = standalone_target(source_path, temp_dir)
        target.build()
        # print out all files in temp_dir
        for file in temp_dir.iterdir():
            print(file.name)
        # read the generated HTML file
        return (temp_dir / "article.html").read_text()
