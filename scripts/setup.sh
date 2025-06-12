#command_exists () {
#    type "$1" &> /dev/null ;
#}

export halflinac_ROOT="$(cd "$( dirname -- "$0" )/.." && pwd)"
export PATH=$PATH:$halflinac_ROOT
