import gc
import shutil
import tempfile
import unittest
from pathlib import Path

import zvec

import server


class FakeVector(list):
    def astype(self, _dtype):
        return self

    def tolist(self):
        return list(self)


class FakeEmbedder:
    def encode(self, texts, normalize_embeddings=True):
        vectors = []
        for text in texts:
            base = min(len(text), 9) / 10
            vectors.append(FakeVector([base] * server.EMBED_DIM))
        return vectors


class YouTubeTranscriptStorageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="context-engine-tests-"))
        self.original_coll_dir = server.COLL_DIR
        self.original_collections = server.collections
        self.original_embedder = server.embedder
        self.original_threshold = server.DEDUP_SIMILARITY_THRESHOLD

        server.COLL_DIR = self.tempdir
        server.COLL_DIR.mkdir(parents=True, exist_ok=True)
        server.collections = {}
        server.embedder = FakeEmbedder()
        server.DEDUP_SIMILARITY_THRESHOLD = 1.0

    def tearDown(self):
        for coll in server.collections.values():
            try:
                coll.optimize()
            except Exception:
                pass
        server.collections = self.original_collections
        server.embedder = self.original_embedder
        server.COLL_DIR = self.original_coll_dir
        server.DEDUP_SIMILARITY_THRESHOLD = self.original_threshold
        gc.collect()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_legacy_add_request_remains_supported(self):
        response = server.add_fact(
            server.AddRequest(
                text="Legacy note",
                collection="legacy-notes",
                source="manual",
                tags=["note"],
            ),
            None,
        )

        self.assertEqual(response["status"], "added")
        results = server.search(
            server.SearchRequest(query="Legacy note", collection="legacy-notes", top_k=1),
            None,
        )["results"]

        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]["source_type"])
        self.assertIsNone(results[0]["metadata"])

    def test_youtube_metadata_round_trips_through_add_and_search(self):
        response = server.add_fact(
            server.AddRequest(
                text="[00:00] Hello world",
                collection="video-notes",
                source="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                tags=["youtube-transcript"],
                source_type="youtube_transcript",
                metadata={
                    "videoId": "dQw4w9WgXcQ",
                    "title": "Public Captioned Demo",
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "language": "en",
                    "isGenerated": False,
                    "method": "page-player-response",
                },
            ),
            None,
        )

        self.assertEqual(response["status"], "added")

        coll = server.get_collection("video-notes")
        stored = coll.fetch(ids=[response["hash"]])[response["hash"]]
        self.assertEqual(stored.fields["source_type"], "youtube_transcript")
        self.assertIn("\"videoId\": \"dQw4w9WgXcQ\"", stored.fields["metadata_json"])

        results = server.search(
            server.SearchRequest(query="Hello world", collection="video-notes", top_k=1),
            None,
        )["results"]

        self.assertEqual(results[0]["source_type"], "youtube_transcript")
        self.assertEqual(results[0]["metadata"]["title"], "Public Captioned Demo")
        self.assertEqual(results[0]["metadata"]["language"], "en")

    def test_collection_schema_migration_preserves_existing_docs_and_vectors(self):
        legacy_path = self.tempdir / "legacy-video"
        legacy_schema = zvec.CollectionSchema(
            name="legacy-video",
            fields=[
                zvec.FieldSchema(name="hash", data_type=zvec.DataType.STRING, nullable=False),
                zvec.FieldSchema(name="text", data_type=zvec.DataType.STRING, nullable=False),
                zvec.FieldSchema(name="source", data_type=zvec.DataType.STRING, nullable=True),
                zvec.FieldSchema(name="agent", data_type=zvec.DataType.STRING, nullable=True),
                zvec.FieldSchema(name="tags", data_type=zvec.DataType.ARRAY_STRING, nullable=True),
                zvec.FieldSchema(name="ts", data_type=zvec.DataType.INT64, nullable=True),
                zvec.FieldSchema(name="embed_model", data_type=zvec.DataType.STRING, nullable=True),
            ],
            vectors=[
                zvec.VectorSchema(
                    name="embedding",
                    data_type=zvec.DataType.VECTOR_FP32,
                    dimension=server.EMBED_DIM,
                ),
            ],
        )
        legacy_coll = zvec.create_and_open(
            str(legacy_path),
            schema=legacy_schema,
            option=server.COLLECTION_OPTION,
        )
        vector = [0.25] * server.EMBED_DIM
        legacy_coll.insert(
            zvec.Doc(
                id="legacy-doc",
                vectors={"embedding": vector},
                fields={
                    "hash": "legacy-doc",
                    "text": "Old transcript text",
                    "source": "manual",
                    "agent": "context-engine",
                    "tags": ["legacy"],
                    "ts": 123,
                    "embed_model": server.MODEL_NAME,
                },
            ),
        )
        legacy_coll.optimize()
        del legacy_coll

        migrated = server.open_collection_with_schema("legacy-video")
        field_names = server.collection_field_names(migrated)

        self.assertIn("source_type", field_names)
        self.assertIn("metadata_json", field_names)

        fetched = migrated.fetch(ids=["legacy-doc"])["legacy-doc"]
        self.assertEqual(fetched.fields["text"], "Old transcript text")
        self.assertNotIn("metadata_json", fetched.fields)
        self.assertAlmostEqual(fetched.vectors["embedding"][0], 0.25, places=5)


if __name__ == "__main__":
    unittest.main()
