# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# backend/models.py
from flask_sqlalchemy import SQLAlchemy
import json
from datetime import datetime # Import datetime

# Initialize SQLAlchemy outside of create_app to avoid circular imports
# This instance will be initialized with the Flask app later.
db = SQLAlchemy()

class Lead(db.Model):
    __tablename__ = 'leads' # Explicitly define table name

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    hotness = db.Column(db.String(50), nullable=False)
    region = db.Column(db.String(100), nullable=False)
    industry = db.Column(db.String(255), nullable=True)
    
    # Store lists (tags, sources) as JSON strings
    # tags = db.Column(db.Text, nullable=True) # Removed tags
    
    confidence = db.Column(db.Float, nullable=True) # Changed from risk to confidence
    # Removed crmStatus as per user request
    explanation = db.Column(db.Text, nullable=True)
    fdi_phase = db.Column(db.String(255), nullable=True)
    sources = db.Column(db.Text, nullable=True) # Text type for potentially longer JSON strings
    source_country = db.Column(db.String(100), nullable=True) # New field for source country

    def to_dict(self):
        """Converts the Lead object to a dictionary, deserializing JSON fields."""
        return {
            "id": self.id,
            "name": self.name,
            "hotness": self.hotness,
            "region": self.region,
            "industry": self.industry,
            "tags": [], # Tags are removed, return an empty list for compatibility
            "confidence": self.confidence, # Changed from risk
            # Removed crmStatus from to_dict
            "explanation": self.explanation,
            "fdi_phase": self.fdi_phase,
            "sources": json.loads(self.sources) if self.sources else [],
            "source_country": self.source_country # Include new field in dict
        }

    def from_dict(self, data):
        """Populates the Lead object from a dictionary, serializing JSON fields."""
        self.name = data.get('name')
        self.hotness = data.get('hotness')
        self.region = data.get('region')
        self.industry = data.get('industry')
        self.confidence = data.get('confidence') # Changed from risk
        # Removed crmStatus from from_dict
        self.explanation = data.get('explanation')
        self.fdi_phase = data.get('fdi_phase')
        self.sources = json.dumps(data.get('sources', []))
        self.source_country = data.get('source_country') # Populate new field

    def __repr__(self):
        return f"<Lead {self.id}: {self.name}>"

class Feedback(db.Model):
    __tablename__ = 'feedback' # Explicitly define table name

    id = db.Column(db.Integer, primary_key=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        """Converts the Feedback object to a dictionary."""
        return {
            "id": self.id,
            "rating": self.rating,
            "comment": self.comment,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }

    def __repr__(self):
        return f"<Feedback {self.id}: {self.rating} stars>"

