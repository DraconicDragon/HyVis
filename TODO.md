# TODO

- supported models as table

- setting for ability to only have X top tags used
  - Scenario: want to use tags from rating category only, but don't want multiple rating tags which is possible if confidence is high enough, so we'd limit that to 1, and choose only the tag with highest confidence

- support for "AND" "OR" etc for tag queries
  - instead of using tag query from config, pull files from an open page in hydrus, saves hassle of dealing with AND/OR stuff and can use all the system tags

- option to remove set tag(s) after successful push (eg temp:tagme)
  - Scenario: user has images without any tags at all, adds temp:tagme to files so tag query in config can find those files, then wants to remove temp tags
    - is there no better option to get untagged files? Does fetching from page work?

- change/make different system because categories can be arbitrary, and "rating" tags for e6 models like JTP3 are in "meta" category, not separate "rating"
- output tag filter
  - Scenario: I want to only have rating tags sent to hydrus; this works with setting "rating" category as only output category, but gets complicated when using models that dont have separate rating category like JTP-3 which put rating tags in "meta" category with other non-rating tags

- more qol stuff
  - Model info should be easier available, eg during errors or trying to discover them and stuff
  - more display, eg what device ended up being used

- README stuff

## Interface/TUI for minimal stuff

- hyvis without arg supplied:
  - opens model info getter
  - config chooser

## lower priority (in order)

- (need more info) magic byte fallback for metadata fetch incase hydrus mime type is "unknown" (suggesting its not set, which would be unexpected)

- configurable metadata fetch batch size (currently 256)

- hydrus file download to memory for remote hydrus

- i completely forgot about this and the project/config isnt set up at all for this but...
  - Scenario: 2 or more models active in the same config - In theory user could want to have model 1's tags sent to tag service A while model 2's tags should be sent to tag service B
    - making 2 separate runs would mean reading the same files for a second time (assuming that in the scenario the 2 models both fit in the first place)
    - pain, do I need new config format

- option to set auto download (huggingface download) off since vibe has it too
  - or just tell people to prefix source option with `local:`

- file domains - unlikely, i dont use them at all and dont plan to
