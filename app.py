import logging
import psycopg2
import os
import html as html_utils
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
import uuid
from json_tools import read_json, write_json
from urllib.parse import parse_qs, quote


USERS_FILE = "users.json"


logging.basicConfig(
    filename="logs/app.log",
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    encoding="utf-8"
)


def get_db_connection():
    return psycopg2.connect(
        dbname="images_db",
        user="postgres",
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST", "db"),
        port=5432
    )

def save_metadata(filename, original_name, size, file_type):
    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            query = """
                INSERT INTO images
                    (filename, original_name, size, file_type)
                VALUES (%s, %s, %s, %s)
            """
            cursor.execute(
                query,
                (filename, original_name, size, file_type)
            )

        conn.commit()

    except Exception:
        conn.rollback()
        logging.exception("DATABASE_ERROR: Failed to save image metadata")
        raise

    finally:
        conn.close()

def get_images(page=1, per_page=10):
    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, filename, original_name,
                       size, upload_time, file_type
                FROM images
                ORDER BY upload_time DESC, id DESC
                LIMIT %s OFFSET %s
            """, (per_page, offset))

            images = cursor.fetchall()

        return images
    finally:
        conn.close()


def count_images():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM images")
            total = cursor.fetchone()[0]
        return total
    finally:
        conn.close()


def delete_image(image_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT filename FROM images WHERE id = %s",
                (image_id,)
            )
            result = cursor.fetchone()

            if result is None:
                return None

            filename = result[0]
            cursor.execute(
                "DELETE FROM images WHERE id = %s",
                (image_id,)
            )

            conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    image_path = Path("images") / filename

    try:
        image_path.unlink(missing_ok=True)
    except OSError as error:
        logging.error(f"Failed to delete image file {filename}: {error}")
        raise

    return True


class ImageHostingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/delete/"):
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.end_headers()
            return

        if self.path.split("?")[0] == "/images-list":
            query = self.path.split("?", 1)[-1]
            params = parse_qs(query)

            try:
                page = int(params.get("page", ["1"])[0])
                if page < 1:
                    page = 1
            except (ValueError, TypeError):
                page = 1

            total_images = count_images()
            total_pages = max(1, (total_images + 9) // 10)

            if page > total_pages:
                self.send_response(303)
                self.send_header(
                    "Location",
                    f"/images-list?page={total_pages}"
                )
                self.end_headers()
                return

            images = get_images(page=page)

            pagination = f"<span>Page {page} of {total_pages}</span>"

            if page > 1:
                pagination = (
                        f'<a href="/images-list?page={page - 1}">Previous</a> '
                        + pagination
                )

            if page < total_pages:
                pagination += (
                    f' <a href="/images-list?page={page + 1}">Next</a>'
                )

            with open("static/image_row.html", "r", encoding="utf-8") as file:
                row_template = file.read()

            rows_html = ""

            for image in images:
                image_id, filename, original_name, size, upload_time, file_type = image

                row = row_template.replace("{{FILENAME}}", html_utils.escape(filename, quote=True))
                row = row.replace("{{IMAGE_ID}}", str(image_id))
                row = row.replace("{{IMAGE_URL}}", html_utils.escape(quote(filename, safe=""), quote=True))
                row = row.replace("{{ORIGINAL_NAME}}", html_utils.escape(original_name, quote=True))
                row = row.replace("{{SIZE}}", f"{size / 1024:.2f}")
                row = row.replace("{{UPLOAD_TIME}}", upload_time.strftime("%Y-%m-%d %H:%M:%S"))
                row = row.replace("{{FILE_TYPE}}", file_type)

                rows_html += row

            if not images:
                rows_html = '<tr><td colspan="6">No images uploaded yet.</td></tr>'


            with open("static/images_list.html", "r", encoding="utf-8") as file:
                html = file.read()

            html = html.replace("{{IMAGE_ROWS}}", rows_html)

            html = html.replace("{{PAGINATION}}", pagination)

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
            return

        if self.path == "/":
            filename = "static/index.html"

        elif self.path == "/about":
            filename = "static/about.html"

        elif self.path == "/products":
            filename = "static/products.html"

        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>404 Not Found</h1>")
            return

        with open(filename, "rb") as file:
            content = file.read()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        if self.path.startswith("/delete/"):
            image_id = self.path.split("/")[-1]
            if not image_id.isdigit():
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid image ID")
                return

            image_id = int(image_id)

            try:
                result = delete_image(image_id)

            except Exception:
                logging.exception("Failed to delete image")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Internal Server Error")
                return

            if result is None:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Image not found")
                return

            self.send_response(303)
            self.send_header("Location", "/images-list")
            self.end_headers()
            return

        if self.path == "/users":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")

            data = parse_qs(body)

            name = data.get("name", [""])[0].strip()
            lastname = data.get("lastname", [""])[0].strip()

            if not name or not lastname:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Name and lastname are required")
                return

            users = read_json(USERS_FILE)

            users.append({
                "name": name,
                "lastname": lastname
            })

            write_json(USERS_FILE, users)

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                f"<h1>Thank you, {name} {lastname}!</h1>".encode("utf-8")
            )
            return

        if self.path == "/upload":
            content_type = self.headers.get("Content-Type")

            if not content_type:
                logging.warning("UPLOAD_ERROR: Content-Type header is missing")
                self.send_error(400, "Content-Type header is missing")
                return

            content_length = int(self.headers.get("Content-Length", 0))

            body = self.rfile.read(content_length)

            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
            )

            for part in message.iter_parts():
                if part.get_param("name", header="content-disposition") == "image":
                    filename = part.get_filename()

                    if not filename:
                        logging.warning("UPLOAD_ERROR: file was not selected")
                        self.send_error(400, "File was not selected")
                        return

                    extension = Path(filename).suffix.lower()

                    allowed_extensions = {".jpg", ".png", ".gif"}

                    if extension not in allowed_extensions:
                        logging.warning(
                            "UPLOAD_ERROR: unsupported file format %s",
                            extension
                        )
                        self.send_error(400, "Unsupported file format")
                        return

                    file_data = part.get_payload(decode=True)

                    max_size = 5 * 1024 * 1024

                    if len(file_data) > max_size:
                        logging.warning(
                            "UPLOAD_ERROR: file %s is larger than 5 MB",
                            filename
                        )
                        self.send_error(400, "File is larger than 5 MB")
                        return

                    unique_filename = f"{uuid.uuid4().hex}{extension}"

                    image_path = Path("images") / unique_filename

                    try:
                        image_path.write_bytes(file_data)

                        save_metadata(
                            filename=unique_filename,
                            original_name=filename,
                            size=len(file_data),
                            file_type=extension.lstrip(".")
                        )

                    except Exception:
                        image_path.unlink(missing_ok=True)

                        logging.exception(
                            "UPLOAD_ERROR: Failed to save image %s",
                            unique_filename
                        )

                        self.send_error(500, "Failed to save image")
                        return

                    logging.info(
                        "UPLOAD: image %s and metadata saved successfully",
                        unique_filename
                    )

                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        "text/html; charset=utf-8"
                    )
                    self.end_headers()

                    image_url = f"http://localhost:8080/images/{unique_filename}"

                    response = (
                        "<h1>Image uploaded successfully</h1>"
                        f"<p>Saved as: {unique_filename}</p>"
                        f'<p>Image URL: <a href="{image_url}">{image_url}</a></p>'
                    )

                    self.wfile.write(response.encode("utf-8"))
                    return

        self.send_response(404)
        self.end_headers()


server = ThreadingHTTPServer(("0.0.0.0", 8000), ImageHostingHandler)

print("Server started at http://localhost:8000")

server.serve_forever()