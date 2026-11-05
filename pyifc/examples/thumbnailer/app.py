from __future__ import annotations

from typing import Any

from pyifc import Application, Bucket, Queue


app = Application("thumbnailer")

uploads = Bucket("uploads")
thumbnails = Bucket("thumbnails")
thumbnail_jobs = Queue("thumbnail-jobs")


@app.write(uploads)
@app.publish(thumbnail_jobs)
@app.handler(
    method="POST",
    path="/images",
    id="upload-handler",
)
async def upload_image(request: dict[str, Any]) -> dict[str, str]:
    """
    Accept an image upload and request thumbnail generation.

    Upload and dispatch operations provide the analyzer's valid use sites.
    """

    uploads.put("image-id", b"image-data")
    thumbnail_jobs.publish(b"image-id")

    return {
        "status": "accepted",
    }


@app.subscribe(thumbnail_jobs)
@app.read(uploads)
@app.write(thumbnails)
@app.worker(id="thumbnail-worker")
async def create_thumbnail(message: dict[str, Any]) -> dict[str, str]:
    """
    Process a thumbnail-generation message.

    Read and write operations provide the analyzer's valid use sites.
    """

    thumbnail_jobs.receive()
    uploads.get("image-id")
    thumbnails.put("thumbnail-id", b"thumbnail-data")

    return {
        "status": "processed",
    }