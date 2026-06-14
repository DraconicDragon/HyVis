# TODO

- (vibe) supported models as table for better discoverabiltiy/readability

- Support for "AND" "OR" etc for tag queries
  - Actually, instead of using tag query from config, pull files from an open page in hydrus, saves hassle of dealing with AND/OR stuff and can use all the system tags

- More QoL stuff
  - Model info should be easier available, eg during errors or trying to discover them and stuff
  - More info at runtime, eg what device ended up being used

- README stuff

## lower priority (in order)

- (need more info) magic byte fallback for metadata fetch incase hydrus mime type is "unknown" (suggesting its not set, which would be unexpected)

- option to remove set tag(s) after successful push (eg temp:tagme)
  - Scenario: user has images without any tags at all, adds temp:tagme to files so tag query in config can find those files, then wants to remove temp tags
    - is there no better option to get untagged files? Does fetching from page work?

- (vibe) store param count for each model(-plugin)
  - what is this useful for
  - I'd have to calculate it using numel()/sfts metadata/onnx graph for each model
    - animetimm models already have info available on their readmes but not the rest
    - No, jtp3 is not 400m params, thats the siglip2 naflex base, jtp3's hydra head and other custom stuff makes it 501m params

- configurable metadata fetch batch size (currently 256)

- hydrus file download to memory for remote hydrus

- option to set auto download (huggingface download) off since vibe has it too
  - or just tell people to prefix source option with `local:`

- file domains - unlikely, i dont use them at all and dont plan to

## Interface/TUI for minimal stuff

- hyvis without arg supplied:
  - opens model info getter
  - config chooser
