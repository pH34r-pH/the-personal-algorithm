"""On-demand YouTube uploads for an imported subscription; no media is copied."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse

from .connections import ConnectionStore
from .providers import ConnectionStatus
from .store import Store

_CHANNEL = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_PLAYLIST = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_API = "https://www.googleapis.com/youtube/v3"


def _access_token(connections, auth):
    if auth is None:
        raise HTTPException(status_code=503, detail="Google authorization is not configured")
    try:
        connection = connections.get("google")
        if (connection.status != ConnectionStatus.CONNECTED or
                "https://www.googleapis.com/auth/youtube.readonly" not in connection.granted_scopes):
            raise ValueError("connection unavailable")
        secret = json.loads(auth.credentials.get(connection.credential_ref))
        access = secret["access_token"]
        expires = datetime.fromisoformat(secret["expires_at"])
        if not isinstance(access, str) or not access or expires.tzinfo is None:
            raise ValueError("invalid credential")
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Connect Google before loading videos") from exc
    if expires <= datetime.now(UTC):
        raise HTTPException(status_code=409, detail="Refresh the Google connection before loading videos")
    return access


def _video_links(entries):
    if not isinstance(entries, list) or len(entries) > 10:
        raise ValueError("invalid playlist response")
    videos = []
    for item in entries:
        video_id = item["contentDetails"]["videoId"]
        snippet = item["snippet"]
        title = snippet["title"]
        if not isinstance(video_id, str) or not _VIDEO.fullmatch(video_id):
            continue
        if not isinstance(title, str):
            continue
        videos.append({"id": video_id, "title": title,
                       "published_at": snippet.get("publishedAt"),
                       "url": f"https://www.youtube.com/watch?v={video_id}",
                       "embed_url": f"https://www.youtube.com/embed/{video_id}"})
    return videos


def _fetch_videos(client, channel_id, access):
    headers = {"Authorization": f"Bearer {access}"}
    try:
        channel = client.get(f"{_API}/channels", headers=headers,
                             params={"part": "contentDetails", "id": channel_id})
        channel.raise_for_status()
        items = channel.json()["items"]
        if len(items) != 1 or items[0].get("id") != channel_id:
            raise ValueError("channel unavailable")
        playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        if not isinstance(playlist, str) or not _PLAYLIST.fullmatch(playlist):
            raise ValueError("invalid playlist")
        feed = client.get(f"{_API}/playlistItems", headers=headers,
                          params={"part": "snippet,contentDetails", "playlistId": playlist,
                                  "maxResults": 10})
        feed.raise_for_status()
        return _video_links(feed.json()["items"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as exc:
        raise HTTPException(status_code=502, detail="YouTube discovery failed") from exc


def create_youtube_router(store: Store, connections: ConnectionStore, auth, *, http=None):
    router = APIRouter(prefix="/youtube", tags=["youtube"])
    client = http or httpx.Client(timeout=20.0, follow_redirects=False)

    @router.get("/", response_class=HTMLResponse)
    def page():
        return youtube_page()

    @router.get("/subscriptions")
    def subscriptions(response: Response):
        response.headers["Cache-Control"] = "no-store"
        rows = store.connection.execute(
            """SELECT subject, canonical_url, payload_json FROM personal_events
               WHERE source='youtube' AND event_type='subscription' ORDER BY subject COLLATE NOCASE"""
        ).fetchall()
        values = []
        for title, url, payload in rows:
            try:
                channel_id = json.loads(payload)["channel_id"]
            except (ValueError, KeyError, TypeError):
                continue
            if isinstance(channel_id, str) and _CHANNEL.fullmatch(channel_id):
                values.append({"channel_id": channel_id, "title": title, "url": url})
        return {"subscriptions": values}

    @router.get("/recent")
    def recent(channel_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if not _CHANNEL.fullmatch(channel_id):
            raise HTTPException(status_code=422, detail="Invalid YouTube channel ID")
        row = store.connection.execute(
            "SELECT 1 FROM personal_events WHERE id=? AND source='youtube' AND event_type='subscription'",
            (f"youtube:subscription:{channel_id}",),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Channel is not an imported subscription")
        videos = _fetch_videos(client, channel_id, _access_token(connections, auth))
        return {"source": "YouTube", "channel_id": channel_id, "videos": videos}

    return router


def youtube_page() -> str:
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>YouTube discovery</title>
<style>body{font:16px system-ui;background:#101116;color:#f4f1e8;margin:2rem auto;max-width:800px;padding:0 1rem}
a{color:#adf58e}select,button{font:inherit;padding:.6rem;margin:.5rem}li{margin:1rem 0}
iframe{width:min(100%,640px);aspect-ratio:16/9;border:0}</style></head><body>
<a href="/app/">← Private home</a><h1>YouTube discovery</h1>
<p>Choose an imported channel to request its recent uploads from YouTube. Results are not stored or ranked.</p>
<label for="channel">Channel</label><select id="channel"></select><button id="load">Load recent videos</button>
<p id="status" role="status"></p><ul id="results"></ul><div id="player"></div>
<script>
const channel=document.querySelector('#channel'), status=document.querySelector('#status');
const results=document.querySelector('#results'), player=document.querySelector('#player');
fetch('/youtube/subscriptions').then(r=>{if(!r.ok)throw Error('Cannot load subscriptions');return r.json()})
.then(data=>{for(const item of data.subscriptions){const o=document.createElement('option');
o.value=item.channel_id;o.textContent=item.title;channel.append(o)}
status.textContent=data.subscriptions.length ? 'Choose a channel.' : 'Import YouTube subscriptions first.'})
.catch(e=>status.textContent=e.message);
document.querySelector('#load').addEventListener('click',async()=>{
 results.replaceChildren();player.replaceChildren();if(!channel.value)return;
 status.textContent='Loading from YouTube…';
 try{const response=await fetch('/youtube/recent?channel_id='+encodeURIComponent(channel.value));
 const data=await response.json();if(!response.ok)throw Error(data.detail || 'Could not load videos');
 for(const video of data.videos){const li=document.createElement('li'), link=document.createElement('a');
 link.href=video.url;link.textContent=video.title;link.target='_blank';link.rel='noopener noreferrer';
 const play=document.createElement('button');play.textContent='Play here';play.addEventListener('click',()=>{
 const frame=document.createElement('iframe');frame.src=video.embed_url;frame.title=video.title;
 frame.allow='accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture';
 frame.allowFullscreen=true;player.replaceChildren(frame)});li.append(link,play);results.append(li)}
 status.textContent=data.videos.length ? 'Results from YouTube.' : 'No available uploads returned.';
 }catch(e){status.textContent=e.message}
});</script></body></html>"""
