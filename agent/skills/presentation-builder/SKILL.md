---
skill: presentation-builder
version: 1.0.0
author: openclaw-agent
created: 2026-03-01T11:02:58.475494+00:00
trust_level: agent-generated
capabilities:
  - file_read:/workspace/**
  - file_write:/workspace/output/**
  - file_list:/workspace/**
rate_limit: 20/minute
token_budget: 10000
signature: "UNSIGNED — agent-generated"
signed_by: ""
signed_at: ""
---

# SKILL.md — Presentation Builder

## Purpose

Create PowerPoint presentations using python-pptx library

## When to Load

User asks to create a PowerPoint presentation, deck, or slides

## Instructions

# Presentation Builder Skill

## When to use
Use this skill when the user asks to create a PowerPoint presentation (.pptx file) about any topic.

## How to use
1. First, ensure python-pptx is installed: `pip install python-pptx`
2. Create a Python script that uses the pptx library to build slides
3. Structure the presentation with:
   - Title slide
   - Content slides with bullet points
   - Visual elements (shapes, text boxes)
   - Consistent formatting
4. Save the presentation to the output directory: `output/presentation.pptx`
5. Provide a preview or summary of the slides created

## Example structure
```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor

# Create presentation
prs = Presentation()

# Add slides with different layouts
title_slide_layout = prs.slide_layouts[0]
content_slide_layout = prs.slide_layouts[1]

# Add title slide
slide = prs.slides.add_slide(title_slide_layout)
title = slide.shapes.title
subtitle = slide.placeholders[1]
title.text = "Presentation Title"
subtitle.text = "Subtitle or Author"

# Add content slides
slide = prs.slides.add_slide(content_slide_layout)
title = slide.shapes.title
title.text = "Slide Title"
content = slide.placeholders[1]
content.text = "• First bullet point\n• Second bullet point\n• Third bullet point"

# Save presentation
prs.save('output/presentation.pptx')
```

## Best practices
- Use consistent fonts and colors
- Keep text concise (6x6 rule: 6 lines, 6 words per line)
- Add relevant shapes or icons for visual appeal
- Include a title slide and conclusion slide

## Constraints

- **Agent-generated skill** — restricted capabilities, sandboxed by default
- Must be signed with `ccos-sign sign agent/skills/presentation-builder` before production use
- Rate limited to 20 requests/minute
- Token budget: 10,000 (prevents context bloat — Eureka #7 Shannon SNR)
- No network access, no exec, no credential access
- All tool calls mediated by AgentProxy (Layer 4)

## Audit Trail

This skill was dynamically created by OpenClaw at 2026-03-01T11:02:58.475494+00:00.
