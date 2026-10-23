/* ShortsScheduler.app's executable: a tiny native (arm64 + x86_64) program that runs the
 * bundle's Resources/launch.sh in the same process. A script can't be an app's executable on
 * Apple silicon: macOS can't tell its architecture and asks to install Rosetta.
 *
 * Built once into assets/launcher (so nobody needs developer tools):
 *   clang -arch arm64 -arch x86_64 -mmacosx-version-min=12.0 -O2 \
 *     -o assets/launcher scripts/launcher.c
 */
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(void) {
    char executable[PATH_MAX], resolved[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof executable;
    if (_NSGetExecutablePath(executable, &size) != 0 || !realpath(executable, resolved)) {
        fputs("ShortsScheduler: cannot find its own location\n", stderr);
        return 1;
    }
    snprintf(script, sizeof script, "%s/../Resources/launch.sh", dirname(resolved));
    char *arguments[] = {"/bin/zsh", script, NULL};
    execv("/bin/zsh", arguments); /* same process: the Dock keeps the app's name and icon */
    perror("ShortsScheduler: cannot start");
    return 1;
}
