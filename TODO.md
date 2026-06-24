# TODO

- Character IP mapping support
  - (vibe) also find out how to make my own or if i just need full metadata for that and have the math done on full post metadata to see how often series tags are together with what character tags, maybe some of the e6 people already have some process open source

- Something like max_tags_per_category but for a collection of tags
  - This is for when user wants to use max_tags_per_category for rating tags, but JTP doesnt have rating tags in separate category, so it'd be possible to put all those 3 rating tags in a collection and if 2 or 3 tags are above threshold for a file, itd only pick n tags starting with highest threshold

- (vibe - ModelPlugin Metadata) Recommended thresholds for each model, in extra column in SUPPORTED_MODELS.md

- More QoL stuff
  - Model info should be easier available, eg during errors or trying to discover them and stuff
  - More info at runtime, eg what device ended up being used

## lower priority (in order, mostly)

- (vibe) store param count for each model(-plugin)
  - what is this useful for
  - I'd have to calculate it using numel()/sfts metadata/onnx graph for each model
    - animetimm models already have info available on their readmes but not the rest
    - No, jtp3 is not 400m params, thats the siglip2 naflex base, jtp3's hydra head and other custom stuff makes it 501m params
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

- hydrus file download to memory for remote clients/file locations

- option to set auto download (huggingface download) off since vibe has it too
  - or just tell people to prefix source option with `local:`

- [x] Support for pulling files from an open page in hydrus
  - [ ] In theory could also work with tag file queries, we apply the query instead of just all tags in a domain to the domain, but only on files that are in the page(s) - need to confirm if this will actually work easily

- [x] Preview for file queries using an open page in Hydrus
  - [ ] mark experimental since i think API says that these endpoints are experimental

- file domains - unlikely, i dont use them at all and dont plan to

## Interface/TUI for minimal stuff

- hyvis without arg supplied:
  - opens model info getter
  - config chooser
