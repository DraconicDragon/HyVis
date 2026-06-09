# TODO

- (vibe) supported models as table for better discoverabiltiy/readability

- Support for "AND" "OR" etc for tag queries
  - Actually, instead of using tag query from config, pull files from an open page in hydrus, saves hassle of dealing with AND/OR stuff and can use all the system tags

- <a id="output_rework_output_filter"></a>Put all output stuff into one section - Output filter
  - Make it per model and global?
  - Include inference option and .category_thresholds in this
    - most options can be per model too
    - Should have per-tag threshold override
      - Option to allow a specific override to affect TLT too - does toml support json-like?
  - Include `tag_prefix_mapping` in this too, per model and global
  - Tag and category inclusion/exclusion
    - Scenario A: I want to only have rating tags sent to hydrus; this works with setting "rating" category as only output category, but gets complicated when using models that dont have separate rating category like JTP-3 which put rating tags in "meta" category with other non-rating tags
    - Scenario B:
  - Ability to set only top X tags used
    - Scenario: User wants tags from `rating` category only, but doesn't want multiple rating tags, which is possible if confidence is high enough, so we'd limit max tags of a category to push to 1, and choose only the tag(s) with highest confidence/confidences (descending)
  - Related: [per-model output tag services](#output_tag_services_restructure)

- More QoL stuff
  - Model info should be easier available, eg during errors or trying to discover them and stuff
  - More info at runtime, eg what device ended up being used

- README stuff

## lower priority (in order)

- (need more info) magic byte fallback for metadata fetch incase hydrus mime type is "unknown" (suggesting its not set, which would be unexpected)

- <a id="output_tag_services_restructure"></a>Make `output_tag_services` per-model
  - related [Output rework](#output_rework_output_filter)
  - Scenario: 2 or more models active in the same config - In theory user could want to have model 1's tags sent to tag service A while model 2's tags should be sent to tag service B
    - making 2 separate runs would mean reading the same files for a second time (assuming that in the scenario the 2 models both fit in the first place)

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
