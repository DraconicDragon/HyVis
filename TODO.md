# TODO

- Support for pulling files from an open page in hydrus
  - In theory could also work with tag file queries, we apply the query instead of just all tags in a domain to the domain, but only on files that are in the page(s) - need to confirm if this will actually work easily
  - Lock down page selection to specific page types (see api docs)
  - API caveat: user can't really specify page ID, only page name. But page names can be dupes, so need a way to specify which one, and also error if 2 pages with same name exist

- Preview for file queries using an open page in Hydrus
  - API caveat: same as pulling files from open page
  - Lock down/require specific/normal page type only
  - Require page to be empty
  - mark experimental since i think API says that these endpoints are experimental

- (vibe - ModelPlugin Metadata) Recommended thresholds for each model, in extra column in SUPPORTED_MODELS.md

- More QoL stuff
  - Model info should be easier available, eg during errors or trying to discover them and stuff
  - More info at runtime, eg what device ended up being used

## lower priority (in order)

- (need more info) magic byte fallback for metadata fetch incase hydrus mime type is "unknown" (suggesting its not set, which would be unexpected)

- (vibe) store param count for each model(-plugin)
  - what is this useful for
  - I'd have to calculate it using numel()/sfts metadata/onnx graph for each model
    - animetimm models already have info available on their readmes but not the rest
    - No, jtp3 is not 400m params, thats the siglip2 naflex base, jtp3's hydra head and other custom stuff makes it 501m params

- its gonna be weird but: video/animation support, could support multiple modes
  - every frame (too heavy but an option, maybe for gifs/very short vids)
  - every nth frame
  - keyframes only (if possible)
  - do analysis and find frames with cuts or scene changes

- configurable metadata fetch batch size (currently 256 hardcoded)

- hydrus file download to memory for remote clients/file locations

- option to set auto download (huggingface download) off since vibe has it too
  - or just tell people to prefix source option with `local:`

- file domains - unlikely, i dont use them at all and dont plan to

## Interface/TUI for minimal stuff

- hyvis without arg supplied:
  - opens model info getter
  - config chooser
