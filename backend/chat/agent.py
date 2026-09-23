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

import os
from google.adk.agents import Agent
from google.genai.types import GenerateContentConfig

# Import the agent's prompt and tools
from .prompts import AGENT_PROMPT
from .utils import agent_find_leads, generic_search # Import both tools

# Ensure Google ADK uses Vertex AI with Application Default Credentials (ADC)
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")

# Define the model for the agent
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

# --- Define the Root Agent ---
# This agent orchestrates the conversational experience, using the
# provided prompt for guidance and the tools for action.
root_agent = Agent(
    model=MODEL,
    name="UOB_Leadgen_Assistant", # Added the required name parameter
    # The agent's instruction is the comprehensive prompt we've designed.
    # It tells the agent how to behave, what its goals are, and how to use its tools.
    instruction=AGENT_PROMPT,
    
    # The list of tools the agent can call. The ADK handles the conversion
    # of these Python functions into a format the model can understand.
    tools=[
        agent_find_leads,
        generic_search
    ],
)
