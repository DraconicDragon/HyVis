# TODO

- supported models doc should probably show tag amount and link to them or something, also have vibe have some description and note field(s) or such to be able to give more info on a model or family

- Character IP mapping support
  - (vibe) also find out how to make my own or if i just need full metadata for that and have the math done on full post metadata to see how often series tags are together with what character tags, maybe some of the e6 people already have some process open source

- (vibe - ModelPlugin Metadata) Recommended thresholds for each model, in extra column in SUPPORTED_MODELS.md

## lower priority (in order, mostly)

- (vibe) store param count for each model(-plugin)
  - what is this useful for
  - I'd have to calculate it using numel()/sfts metadata/onnx graph for each model
    - animetimm models already have info available on their readmes but not the rest
    - No, jtp3/hydra 3.5 is not 400m params, thats the siglip2 naflex base, jtp3's hydra head and other custom stuff makes it 501m params
  - could use jupyter notebook + google colab i guess to download each model and calc count and save

- (v2) its gonna be weird but: video/animation support, could support multiple modes
  - every frame (too heavy but an option, maybe for gifs/very short vids)
  - every nth frame (could do fps percentage based or hard user-set frame intervals)
  - keyframes only (if easily possible)
  - do analysis and find frames with cuts or scene changes

- (need more info) magic byte fallback for metadata fetch incase hydrus mime type is "unknown" (suggesting its not set, which would be unexpected)

- (v2) support for removing tags if confidence is below a certain threshold
  - can be useful if there's wrong tags either from original source or from old model i guess?

- configurable metadata fetch batch size (currently 256 hardcoded)

- more utilities for db like clear-cache? model_id specific stuff maybe?

- option to set auto download (huggingface download) off since vibe has it too
  - or just tell people to prefix source option with `local:`

- [x] Support for pulling files from an open page in hydrus
  - [ ] In theory could also work with tag tag queries, we apply the query instead of just all tags in a domain to the domain, but only on files that are in the page(s) - need to confirm if this will actually work easily

- [x] Preview for tag queries using an open page in Hydrus
  - [ ] mark experimental since i think API says that these endpoints are experimental

- hydrus file download to memory for remote clients/file locations

- file domains - unlikely, i dont use them at all and dont plan to

## Interface/TUI for minimal stuff

- hyvis without arg supplied:
  - opens model info getter
  - config chooser
